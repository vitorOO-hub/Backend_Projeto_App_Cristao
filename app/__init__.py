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
from app.errors import ApiError, CacheUnavailable, AuthUnavailable, DataUnavailable, StorageUnavailable
from app.auth import auth, configure_auth
from app.media import probe_video
from app.interactions import MongoInteractions, UnavailableInteractions
from app.moderation_api import moderation
from app.purge import purge_pending, recheck_deleted
from app.orphans import MongoOrphanRepository, UnavailableOrphanRepository
from app.access import parse_id
from app.profile_api import profile
from app.social_api import social
from app.storage import LocalStorage
from app.upload_request import UploadRequest
from app.uploads import MongoUploadRepository, UnavailableUploadRepository, UploadManager
from app.video_api import videos
from app.auth_repository import MongoAuthRepository, UnavailableAuthRepository
from app.social_repository import (MongoBlockRepository, MongoCommentRepository,
                                   MongoFollowRepository, MongoReactionRepository, MongoReportRepository,
                                   UnavailableBlockRepository, UnavailableCommentRepository,
                                   UnavailableFollowRepository, UnavailableReactionRepository,
                                   UnavailableReportRepository)
from app.video_repository import MongoVideoRepository, UnavailableVideoRepository
from app.security import (MongoRateLimits, SQLiteRateLimits, configure_security,
                          protect_requests, validate_mongo_transport)


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True, static_folder=None)
    app.request_class = UploadRequest
    app.config.from_mapping(
        APP_ENV=os.getenv("APP_ENV", "development"),
        APP_ORIGIN=os.getenv("APP_ORIGIN", "http://localhost:5173"),
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "true").lower() == "true",
        ANONYMOUS_SESSION_TTL_SECONDS=int(os.getenv("ANONYMOUS_SESSION_TTL_SECONDS", "1800")),
        SESSION_TTL_SECONDS=int(os.getenv("SESSION_TTL_SECONDS", "43200")),
        JWT_SECRET=os.getenv("JWT_SECRET", ""),
        MONGODB_URI=os.getenv("MONGODB_URI", ""),
        MONGODB_DATABASE=os.getenv("MONGODB_DATABASE", "app_cristao_dev"),
        TRUSTED_PROXY_IPS=os.getenv("TRUSTED_PROXY_IPS", ""),
        BIBLE_API_BASE_URL=os.getenv("BIBLE_API_BASE_URL", "https://bible-api.com"),
        BIBLE_CONNECT_TIMEOUT_SECONDS=float(os.getenv("BIBLE_CONNECT_TIMEOUT_SECONDS", "2")),
        BIBLE_READ_TIMEOUT_SECONDS=float(os.getenv("BIBLE_READ_TIMEOUT_SECONDS", "5")),
        BIBLE_CACHE_TTL_SECONDS=int(os.getenv("BIBLE_CACHE_TTL_SECONDS", "86400")),
        MAX_CONTENT_LENGTH=int(os.getenv("MAX_REQUEST_BYTES", "53477376")),
        MAX_VIDEO_BYTES=int(os.getenv("MAX_VIDEO_BYTES", "52428800")),
        MAX_USER_STORAGE_BYTES=int(os.getenv('MAX_USER_STORAGE_BYTES', '1073741824')),
        MAX_PENDING_VIDEOS=int(os.getenv('MAX_PENDING_VIDEOS', '10')),
        MAX_STORAGE_USED_PERCENT=int(os.getenv('MAX_STORAGE_USED_PERCENT', '80')),
        MIN_STORAGE_FREE_BYTES=int(os.getenv('MIN_STORAGE_FREE_BYTES', '2147483648')),
        UPLOAD_VOLUME_ID=os.getenv('UPLOAD_VOLUME_ID', 'local-media'),
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
            ("block", MongoBlockRepository, UnavailableBlockRepository),
            ("follow", MongoFollowRepository, UnavailableFollowRepository)):
        app.extensions[f"{name}_repository"] = app.config.get(f"{name.upper()}_REPOSITORY") or (
            real(mongo[app.config["MONGODB_DATABASE"]]) if mongo is not None else offline())
    app.extensions['rate_limits'] = app.config.get('RATE_LIMITS') or (
        MongoRateLimits(mongo[app.config['MONGODB_DATABASE']]['rate_limits']) if mongo is not None
        else SQLiteRateLimits(Path(app.config['CACHE_SQLITE_PATH']).with_name('rate-limits.sqlite3')))
    app.extensions['interactions_service'] = app.config.get('INTERACTIONS_SERVICE') or (
        MongoInteractions(mongo[app.config['MONGODB_DATABASE']]) if mongo is not None
        else UnavailableInteractions())
    app.extensions['orphan_repository'] = app.config.get('ORPHAN_REPOSITORY') or (
        MongoOrphanRepository(mongo[app.config['MONGODB_DATABASE']]) if mongo is not None
        else UnavailableOrphanRepository())
    app.before_request(protect_requests)
    app.register_blueprint(auth)
    app.register_blueprint(videos)
    app.register_blueprint(social)
    app.register_blueprint(profile)
    app.register_blueprint(moderation)
    upload_dir = Path(app.config["UPLOAD_DIR"])
    if not upload_dir.is_absolute():
        upload_dir = Path(app.root_path).parent / upload_dir
    app.extensions["storage"] = app.config.get("STORAGE") or LocalStorage(upload_dir)
    app.extensions['upload_repository'] = app.config.get('UPLOAD_REPOSITORY') or (
        MongoUploadRepository(mongo[app.config['MONGODB_DATABASE']]) if mongo is not None
        else UnavailableUploadRepository())
    app.extensions['upload_manager'] = UploadManager(app.extensions['storage'], app.extensions['upload_repository'],
                                                     app.config, app.config.get('UPLOAD_CAPACITY_PROBE'))
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

    @app.errorhandler(StorageUnavailable)
    def storage_unavailable(error):
        response = jsonify(error={'code': 'storage_unavailable',
                                   'message': 'O armazenamento de vídeos está temporariamente indisponível. Tente novamente mais tarde.'})
        response.status_code = 503
        response.headers['Retry-After'] = '60'
        return response

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
    @click.option('--recheck', is_flag=True, help='Rever também vídeos excluídos já marcados como limpos.')
    @click.option('--limit', type=click.IntRange(1, 100), default=50, show_default=True)
    @click.option('--after', help='Cursor hexadecimal de continuação da revisão explícita.')
    def purge_videos(recheck, limit, after):
        """Finish the physical removal of deleted videos (file, reactions and comments)."""
        try:
            if after is not None and not recheck:
                raise click.ClickException('Use --after somente com --recheck.')
            done, cursor = (recheck_deleted(limit, parse_id(after) if after else None) if recheck
                            else (purge_pending(limit), None))
        except (DataUnavailable, OSError, ApiError):
            raise click.ClickException("Não foi possível concluir a limpeza; tente novamente.") from None
        click.echo(f"Vídeos limpos: {done}.")
        if cursor:
            click.echo(f'Próximo cursor: {cursor}')

    @app.cli.command('audit-orphans')
    @click.option('--collection', 'collection_name', type=click.Choice(['comments', 'reactions']),
                  help='Diagnosticar somente esta coleção; necessário para continuar com --after.')
    @click.option('--limit', type=click.IntRange(1, 100), default=50, show_default=True)
    @click.option('--after', help='Listar relações com _id maior que este cursor hexadecimal.')
    def audit_orphans(collection_name, limit, after):
        """Diagnose orphan comments/reactions without changing files or records."""
        try:
            if after is not None and collection_name is None:
                raise click.ClickException('Use --collection ao continuar um diagnóstico com --after.')
            cursor = parse_id(after) if after else None
            for name in ([collection_name] if collection_name else ('comments', 'reactions')):
                rows, more = app.extensions['orphan_repository'].list_for(name, limit=limit, after=cursor)
                click.echo(f'{name}: {len(rows)} órfãos nesta página.')
                for row in rows:
                    click.echo(f'  id={row["_id"]} video_id={row["video_id"]}')
                if more:
                    click.echo(f'  Próximo cursor ({name}): {rows[-1]["_id"]}')
        except (DataUnavailable, ApiError):
            raise click.ClickException('Não foi possível concluir o diagnóstico.') from None

    @app.cli.command('reconcile-uploads')
    @click.option('--apply', is_flag=True, help='Recuperar explicitamente envios interrompidos; sem esta opção, somente diagnóstico.')
    @click.option('--limit', type=click.IntRange(1, 100), default=50, show_default=True)
    @click.option('--after', help='Cursor UUID hexadecimal de continuação.')
    def reconcile_uploads(apply, limit, after):
        """Diagnose upload workspaces; active process locks are always preserved."""
        try:
            rows, cursor = app.extensions['upload_manager'].reconcile(apply=apply, limit=limit, after=after)
            click.echo('Aplicação explícita.' if apply else 'Somente diagnóstico; nenhum arquivo ou registro alterado.')
            for identity, state in rows:
                click.echo(f'{identity}: {state}')
            if cursor:
                click.echo(f'Próximo cursor: {cursor}')
        except (DataUnavailable, StorageUnavailable, ValueError):
            raise click.ClickException('Não foi possível reconciliar os envios. Nenhum envio ativo será liberado.') from None

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

    @app.cli.command("set-role")
    @click.argument("email")
    @click.argument("role", type=click.Choice(["admin", "user"]))
    def set_role(email, role):
        """Promote or demote an account; the API never changes roles."""
        def apply(target):
            user = target.find_user_by_email(email.strip().lower())
            if user is None:
                raise click.ClickException("Conta não encontrada.")
            target.set_role(user['_id'], role)
        try:
            if app.config['APP_ENV'] == 'production':
                with setup_database() as database:
                    apply(MongoAuthRepository(database))
            else:
                apply(repository)
        except (AuthUnavailable, ValueError, PyMongoError):
            raise click.ClickException("Autenticação indisponível; confira configuração e rede.") from None
        click.echo(f"Papel {role} aplicado; as sessões da conta foram encerradas.")

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
