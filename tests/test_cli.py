def run(world, *args):
    return world.app.test_cli_runner().invoke(args=list(args))


def test_create_admin_promotes_an_existing_account(world):
    user = world.user('Maria')
    email = user.user['email_normalized']
    assert user.get('/api/moderation/videos').status_code == 403
    result = run(world, 'create-admin', f'  {email.upper()}  ')
    assert result.exit_code == 0 and 'administrador' in result.output
    assert world.auth.users[email]['role'] == 'admin'
    assert user.get('/api/moderation/videos').status_code == 401  # promotion revokes the old cookie


def test_create_admin_does_not_create_accounts(world):
    result = run(world, 'create-admin', 'ninguem@example.com')
    assert result.exit_code == 1 and 'Cadastre-se' in result.output
    assert 'Traceback' not in result.output and not world.auth.users


def test_create_admin_outage_is_a_controlled_message(world):
    world.auth.unavailable = True
    result = run(world, 'create-admin', 'maria@example.com')
    assert result.exit_code == 1 and 'Traceback' not in result.output


def test_create_admin_without_database_has_no_local_fallback(app):
    result = app.test_cli_runner().invoke(args=['create-admin', 'maria@example.com'])
    assert result.exit_code == 1 and 'Traceback' not in result.output


def test_no_http_route_can_grant_the_admin_role(world):
    user = world.user('Maria')
    email = user.user['email_normalized']
    assert user.patch('/api/me', json={'role': 'admin'}).status_code == 400
    assert user.patch('/api/me', json={'name': 'Maria', 'role': 'admin'}).status_code == 400
    anonymous = world.anonymous_session()
    body = {'name': 'Mallory', 'email': 'mallory@example.com', 'password': 'a-long-password-123', 'role': 'admin'}
    assert anonymous.post('/api/users', json=body).status_code == 400
    assert world.auth.users[email]['role'] == 'user'


def test_purge_videos_command_finishes_pending_cleanup(world):
    author = world.user()
    video = world.video(author)
    world.videos.soft_delete(video['_id'], author.id, world.time[0])
    assert world.storage.path(video['file']['storage_key']).exists()
    result = run(world, 'purge-videos')
    assert result.exit_code == 0 and 'Vídeos limpos: 1' in result.output
    assert not world.storage.path(video['file']['storage_key']).exists()
    assert 'Vídeos limpos: 0' in run(world, 'purge-videos').output


def test_purge_videos_reports_outage_without_traceback(world):
    world.videos.down = True
    result = run(world, 'purge-videos')
    assert result.exit_code == 1 and 'Traceback' not in result.output
