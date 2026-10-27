import os
from contextlib import contextmanager
from functools import partial
from pathlib import Path

import click
from flask import Blueprint, Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from werkzeug.exceptions import HTTPException

from app.bible import BibleService
from app.cache import MongoCache, SQLiteCache
from app.db import ensure_indexes
from app.errors import ApiError, CacheUnavailable, AuthUnavailable, DataUnavailable
from app.auth import auth, configure_auth
from app.media import probe_video
from app.moderation_api import moderation
from app.purge import purge_pending
from app.social_api import social
from app.storage import LocalStorage
from app.video_api import videos
from app.auth_repository import MongoAuthRepository, UnavailableAuthRepository
from app.social_repository import (MongoBlockRepository, MongoCommentRepository,
                                   MongoReactionRepository, MongoReportRepository,
                                   UnavailableBlockRepository, UnavailableCommentRepository,
                                   UnavailableReactionRepository, UnavailableReportRepository)
from app.video_repository import MongoVideoRepository, UnavailableVideoRepository
from app.security import (MongoRateLimits, SQLiteRateLimits, configure_security,
                          protect_requests, validate_mongo_transport)


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True, static_folder=None)
    app.config.from_mapping(
        APP_ENV=os.getenv("APP_ENV", "development"),
        APP_ORIGIN=os.getenv("APP_ORIGIN", "http://localhost:5173"),
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true",
        ANONYMOUS_SESSION_TTL_SECONDS=int(os.getenv("ANONYMOUS_SESSION_TTL_SECONDS", "1800")),
        SESSION_TTL_SECONDS=int(os.getenv("SESSION_TTL_SECONDS", "43200")),
        MONGODB_URI=os.getenv("MONGODB_URI", ""),
        MONGODB_DATABASE=os.getenv("MONGODB_DATABASE", "app_cristao_dev"),
        TRUSTED_PROXY_IPS=os.getenv("TRUSTED_PROXY_IPS", ""),
        BIBLE_API_BASE_URL=os.getenv("BIBLE_API_BASE_URL", "https://bible-api.com"),
        BIBLE_CONNECT_TIMEOUT_SECONDS=float(os.getenv("BIBLE_CONNECT_TIMEOUT_SECONDS", "2")),
        BIBLE_READ_TIMEOUT_SECONDS=float(os.getenv("BIBLE_READ_TIMEOUT_SECONDS", "5")),
        BIBLE_CACHE_TTL_SECONDS=int(os.getenv("BIBLE_CACHE_TTL_SECONDS", "86400")),
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_REQUEST_BYTES", "53477376")),
        MAX_VIDEO_BYTES=int(os.getenv("MAX_VIDEO_BYTES", "52428800")),
        UPLOAD_DIR=os.getenv("UPLOAD_DIR", "instance/uploads"),
        FFPROBE_PATH=os.getenv("FFPROBE_PATH", "ffprobe"),
        CACHE_SQLITE_PATH=str(Path(app.instance_path) / "bible-cache.sqlite3"),
    )
    if config:
        app.config.update(config)
    if app.config["BIBLE_API_BASE_URL"] != "https://bible-api.com":
        raise ValueError("BIBLE_API_BASE_URL deve ser https://bible-api.com.")
    for key in ("BIBLE_CONNECT_TIMEOUT_SECONDS", "BIBLE_READ_TIMEOUT_SECONDS", "BIBLE_CACHE_TTL_SECONDS"):
        if not 0 < app.config[key] <= 86400:
            raise ValueError(f"Configuração inválida: {key}.")
    if app.config["APP_ENV"] not in ("development", "production", "testing"):
        raise ValueError("APP_ENV inválido.")
    configure_auth(app)
    configure_security(app)
    mongo = None
    if app.config["MONGODB_URI"]:
        mongo = MongoClient(app.config["MONGODB_URI"], serverSelectionTimeoutMS=2000,
                            connectTimeoutMS=2000, socketTimeoutMS=2000, tz_aware=True)
        app.extensions["mongo"] = mongo
    repository = app.config.get("AUTH_REPOSITORY")
    if repository is None:
        repository = (MongoAuthRepository(mongo[app.config["MONGODB_DATABASE"]])
                      if mongo is not None else UnavailableAuthRepository())
    app.extensions["auth_repository"] = repository
    for name, real, offline in (
            ("video", MongoVideoRepository, UnavailableVideoRepository),
            ("reaction", MongoReactionRepository, UnavailableReactionRepository),
            ("comment", MongoCommentRepository, UnavailableCommentRepository),
            ("report", MongoReportRepository, UnavailableReportRepository),
            ("block", MongoBlockRepository, UnavailableBlockRepository)):
        app.extensions[f"{name}_repository"] = app.config.get(f"{name.upper()}_REPOSITORY") or (
            real(mongo[app.config["MONGODB_DATABASE"]]) if mongo is not None else offline())
    app.extensions['rate_limits'] = app.config.get('RATE_LIMITS') or (
        MongoRateLimits(mongo[app.config['MONGODB_DATABASE']]['rate_limits']) if mongo is not None
        else SQLiteRateLimits(Path(app.config['CACHE_SQLITE_PATH']).with_name('rate-limits.sqlite3')))
    app.before_request(protect_requests)
    app.register_blueprint(auth)
    app.register_blueprint(videos)
    app.register_blueprint(social)
    app.register_blueprint(moderation)
    upload_dir = Path(app.config["UPLOAD_DIR"])
    if not upload_dir.is_absolute():
        upload_dir = Path(app.root_path).parent / upload_dir
    app.extensions["storage"] = app.config.get("STORAGE") or LocalStorage(upload_dir)
    app.extensions["media_probe"] = app.config.get("MEDIA_PROBE") or partial(
        probe_video, ffprobe_path=app.config["FFPROBE_PATH"])
    cache = app.config.get("BIBLE_CACHE")
    if cache is None:
        if app.config["MONGODB_URI"]:
            cache = MongoCache(mongo[app.config["MONGODB_DATABASE"]]["bible_cache"])
        elif app.config["APP_ENV"] == "production":
            raise ValueError("Produção exige MONGODB_URI; não há fallback para SQLite.")
        else:
            cache = SQLiteCache(app.config["CACHE_SQLITE_PATH"])
    app.extensions["bible_cache"] = cache
    app.extensions["bible_service"] = BibleService(cache, app.config)
    api = Blueprint("api", __name__, url_prefix="/api")

    @api.get("/health")
    def health():
        return jsonify(data={"status": "ok"})

    @api.get("/bible/passages")
    def passages():
        if any(k not in ("reference", "translation") or len(request.args.getlist(k)) != 1
               for k in request.args):
            raise ApiError(400, "validation_error", "Parâmetros inválidos.")
        return jsonify(data=app.extensions["bible_service"].passage(
            request.args.get("reference"), request.args.get("translation", "almeida")
        ))

    app.register_blueprint(api)

    @app.errorhandler(ApiError)
    def api_error(error):
        response = jsonify(error={"code": error.code, "message": error.message})
        response.status_code = error.status
        if error.retry_after is not None:
            response.headers["Retry-After"] = str(error.retry_after)
        return response

    @app.errorhandler(AuthUnavailable)
    def auth_unavailable(error):
        return jsonify(error={"code": "auth_unavailable", "message": "Serviço temporariamente indisponível."}), 503

    @app.errorhandler(DataUnavailable)
    def data_unavailable(error):
        return jsonify(error={"code": "data_unavailable", "message": "Serviço temporariamente indisponível."}), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        response = error.get_response()
        response.data = app.json.dumps({"error": {"code": "http_error", "message": error.name}})
        response.content_type = "application/json"
        return response

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.error("Erro interno não tratado (%s).", type(error).__name__)
        return jsonify(error={"code": "internal_error", "message": "Erro interno."}), 500

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        if response.headers.get("Cache-Control") != "private, no-store":  # set by the media route
            response.headers["Cache-Control"] = "no-store"
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'none'"
        if app.config['APP_ENV'] == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.cli.command("init-cache")
    def init_cache():
        """Verify cache connectivity and initialize indexes explicitly."""
        try:
            if app.config['APP_ENV'] == 'production':
                initialize_production('cache')
            else:
                cache.initialize()
        except (CacheUnavailable, AuthUnavailable, ValueError, PyMongoError):
            raise click.ClickException("Cache indisponível; confira a configuração e a rede.") from None
        click.echo(f"Cache inicializado: {type(cache).__name__}.")

    @app.cli.command("init-auth")
    def init_auth():
        """Initialize account and session indexes explicitly."""
        try:
            if app.config['APP_ENV'] == 'production':
                initialize_production('auth')
            else:
                repository.initialize()
                app.extensions['rate_limits'].initialize()
        except (AuthUnavailable, ValueError, PyMongoError):
            raise click.ClickException("Autenticação indisponível; confira configuração e rede.") from None
        click.echo("Índices de usuários e sessões inicializados.")

    @app.cli.command("create-admin")
    @click.argument("email")
    def create_admin(email):
        """Give an existing account the admin role; operational use only, no HTTP route does this."""
        normalized = email.strip().lower()
        try:
            if app.config['APP_ENV'] == 'production':
                with setup_database() as database:
                    promoted = MongoAuthRepository(database).promote_user(normalized)
            else:
                promoted = repository.promote_user(normalized)
        except (AuthUnavailable, ValueError, PyMongoError):
            raise click.ClickException("Banco indisponível; confira configuração e rede.") from None
        if not promoted:
            raise click.ClickException("Nenhuma conta com esse e-mail. Cadastre-se primeiro pelo aplicativo.")
        click.echo("Conta promovida a administrador. Ela precisa entrar de novo para usar o painel.")

    @app.cli.command("purge-videos")
    def purge_videos():
        """Finish the physical removal of deleted videos (file, reactions and comments)."""
        try:
            done = purge_pending()
        except (DataUnavailable, OSError):
            raise click.ClickException("Não foi possível concluir a limpeza; tente novamente.") from None
        click.echo(f"Vídeos limpos: {done}.")

    @app.cli.command("init-db")
    def init_db():
        """Create the indexes of the product collections (videos, reactions, comments, reports, blocks)."""
        try:
            if app.config['APP_ENV'] == 'production':
                initialize_production('db')
            elif mongo is not None:
                ensure_indexes(mongo[app.config['MONGODB_DATABASE']])
            else:
                raise click.ClickException("Os dados do produto exigem MONGODB_URI; não há fallback local.")
        except (DataUnavailable, ValueError, PyMongoError):
            raise click.ClickException("Banco indisponível; confira configuração e rede.") from None
        click.echo("Índices das coleções do produto inicializados.")

    @contextmanager
    def setup_database():
        # The web process does not read or need this higher-privilege credential.
        uri = os.getenv('MONGODB_SETUP_URI', '')
        validate_mongo_transport(uri)
        with MongoClient(uri, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000,
                         socketTimeoutMS=2000, tz_aware=True) as setup_client:
            yield setup_client[app.config['MONGODB_DATABASE']]

    def initialize_production(kind):
        with setup_database() as database:
            if kind == 'auth':
                MongoAuthRepository(database).initialize()
                MongoRateLimits(database['rate_limits']).initialize()
            elif kind == 'db':
                ensure_indexes(database)
            else:
                MongoCache(database['bible_cache']).initialize()

    return app
