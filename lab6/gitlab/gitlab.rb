external_url 'https://gitlab.lab.test'

letsencrypt['enable'] = false

nginx['listen_port'] = 80
nginx['listen_https'] = false

nginx['proxy_set_headers'] = {
  'Host' => '$http_host',
  'X-Real-IP' => '$remote_addr',
  'X-Forwarded-For' => '$proxy_add_x_forwarded_for',
  'X-Forwarded-Proto' => 'https',
  'X-Forwarded-Ssl' => 'on'
}

gitlab_rails['trusted_proxies'] = ['172.30.6.10']

gitlab_rails['omniauth_enabled'] = true

gitlab_rails['omniauth_allow_single_sign_on'] = [
  'openid_connect'
]

gitlab_rails['omniauth_block_auto_created_users'] = false

gitlab_rails['omniauth_auto_link_user'] = ['openid_connect']

gitlab_rails['omniauth_providers'] = [
  {
    name: 'openid_connect',
    label: 'Keycloak',

    args: {
      name: 'openid_connect',
      scope: ['openid', 'profile', 'email'],
      response_type: 'code',

      issuer: 'https://sso.lab.test/realms/lab6',

      discovery: true,
      client_auth_method: 'basic',
      uid_field: 'sub',
      pkce: true,

      client_options: {
        identifier: 'gitlab',
        secret: ENV['GITLAB_OIDC_SECRET'],

        redirect_uri: 'https://gitlab.lab.test/users/auth/openid_connect/callback'
      }
    }
  }
]

registry_external_url 'https://registry.lab.test'

registry_nginx['enable'] = false
registry['registry_http_addr'] = '0.0.0.0:5000'

# Снижаем расход ресурсов локального стенда.
puma['worker_processes'] = 0
sidekiq['concurrency'] = 5

# Мониторинг этой работы запускаем отдельно.
prometheus_monitoring['enable'] = false