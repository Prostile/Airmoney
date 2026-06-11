# Airmoney VPN

Docker Compose запускает `container-1` как sing-box gateway. Приложение `airmoney-app` использует сетевой namespace этого контейнера, поэтому nginx может ходить на `http://container-1:8000`, а исходящий трафик приложения идёт через VPN.

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

Не копируй Windows-пути вроде `F:\\...` в серверный конфиг. Для cache используй `/var/lib/sing-box/cache.db`. Если нужны локальные `.srs` rule-set файлы, положи их в `vpn/` и смонтируй отдельным volume.

## Ubuntu Server

```bash
sudo modprobe tun
docker compose up -d --build
docker compose ps
```

Проверка внешнего IP из контейнера приложения:

```bash
docker compose exec airmoney-app python -c "import urllib.request; print(urllib.request.urlopen('https://api.ipify.org', timeout=15).read().decode())"
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
