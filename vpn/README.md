# Airmoney VPN

Docker Compose запускает `container-1` как sing-box gateway. Приложение `airmoney-app` использует сетевой namespace этого контейнера, поэтому nginx может ходить на `http://container-1:8000`, а исходящий трафик приложения идёт через VPN.

`airmoney-app` дополнительно получает proxy env-переменные на `http://127.0.0.1:10808`. Это нужно, чтобы Python, Telegram, получение валют и Playwright не зависели от DNS внутри app-контейнера: DNS для внешних сайтов выполняет sing-box.

## Настройка

```bash
mkdir -p vpn
cp vpn/sing-box.example.json vpn/sing-box.json
```

В `vpn/sing-box.json` перенеси реальные поля из своего VPN-конфига:

- `outbounds[0].server`
- `outbounds[0].server_port`
- `outbounds[0].uuid`
- `outbounds[0].flow`
- `outbounds[0].packet_encoding`
- `outbounds[0].tls.server_name`
- DNS/routing правила, если они отличаются
- inbound `mixed` на `127.0.0.1:10808` должен остаться включённым

Не копируй Windows-пути вроде `F:\\...` в серверный конфиг. Для cache используй `/var/lib/sing-box/cache.db`. Если нужны локальные `.srs` rule-set файлы, положи их в `vpn/` и смонтируй отдельным volume.

## Ubuntu Server

```bash
sudo modprobe tun
docker compose up -d --build --force-recreate
docker compose ps
```

Проверка внешнего IP из контейнера приложения:

```bash
docker compose exec airmoney-app python -c "import urllib.request; print(urllib.request.urlopen('https://api.ipify.org', timeout=15).read().decode())"
```

Если видишь `Temporary failure in name resolution`, проверь, что контейнер был пересоздан после обновления compose и что proxy-переменные есть внутри приложения:

```bash
docker compose exec airmoney-app env | grep -i proxy
docker compose logs --tail=100 container-1
```

Ожидаемые значения:

```text
HTTP_PROXY=http://127.0.0.1:10808
HTTPS_PROXY=http://127.0.0.1:10808
AIRMONEY_BROWSER_PROXY=http://127.0.0.1:10808
```

## Nginx

Nginx-контейнер должен быть подключён к сети `airmoney_net`:

```bash
docker network connect airmoney_net nginx
```

Proxy target:

```nginx
proxy_pass http://container-1:8000;
```
