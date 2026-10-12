# wslmetrics

TUI no terminal para monitorar **CPU** e **RAM** no WSL: top processos (agrupados ou por PID), containers Docker, diffs de RAM, histórico com gráficos, alertas e export CSV.

## Requisitos

- Python 3.11+
- WSL2 (Linux)
- Docker acessível via socket (`unix:///var/run/docker.sock`) — opcional
- Para jobs/alertas em background: **systemd** habilitado no WSL
- Para notificações desktop: `notify-send` (WSLg / libnotify)

## Instalação

### Com pipx (recomendado)

Isola o app no seu próprio ambiente e expõe o comando `wslmetrics` no PATH:

```bash
pipx install /path/to/wslmetrics
# desenvolvimento (editable):
pipx install -e /path/to/wslmetrics
wslmetrics --help
```

Atualizar depois de mudanças no código:

```bash
pipx reinstall wslmetrics
# ou, se instalou com -e, basta editar o código
```

### Com venv

```bash
cd /path/to/wslmetrics
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Dev + testes

```bash
pip install -e ".[dev]"
pytest
pytest --cov=wslmetrics --cov-report=term-missing
```

### Binário único (PyInstaller)

Gera `dist/wslmetrics` para Linux/WSL (ainda usa Docker socket / systemd do host):

```bash
chmod +x scripts/build_binary.sh
./scripts/build_binary.sh
./dist/wslmetrics --help
```

Docker (opcional):

```bash
sudo usermod -aG docker "$USER"
```

Systemd no WSL — necessário para `install-job`. Em `/etc/wsl.conf`:

```ini
[boot]
systemd=true
```

Depois, no Windows: `wsl --shutdown`, reabra o WSL e confira:

```bash
systemctl --user status
```

Se o timer precisar rodar sem sessão interativa:

```bash
loginctl enable-linger "$USER"
```

Sem systemd, use `wslmetrics sample` manualmente (ou um cron próprio).

## Uso

```bash
wslmetrics                 # TUI (default)
wslmetrics --interval 2
wslmetrics --top 50
```

### Teclas (TUI)

| Tecla | Ação                                      |
|-------|-------------------------------------------|
| `q`   | Sair                                      |
| `r`   | Refresh manual                            |
| `c`   | Ordenar por CPU (Processes + Containers)  |
| `m`   | Ordenar por RAM (Processes + Containers)  |
| `g`   | Agrupar processos por nome (`chrome × N`) |
| `t`   | Alterar `top` na sessão                   |
| `i`   | Alterar `interval` (s) na sessão          |
| `/`   | Filtrar por nome (Processes + Containers) |
| `Esc` | Fechar prompt / limpar filtro             |

Com **GROUP ON**, aparece um badge amarelo e o subtítulo `GROUP ON` no header.

Abas:

- **Processes** — por PID ou agrupado (`g`)
- **Containers** — sort `c`/`m` + filtro `/`
- **Diffs** — quem cresceu de RAM desde o último sample e na última 1h
- **History** — gráficos separados de RAM% e CPU%

## Histórico e jobs

O histórico **não depende da TUI**. Um job `systemd --user` coleta samples periodicamente.

```bash
wslmetrics sample          # uma coleta manual → SQLite + alertas
wslmetrics install-job     # instala/liga o timer (~30s)
wslmetrics status          # timer, último sample, alertas recentes
wslmetrics uninstall-job
```

Dados em `~/.local/state/wslmetrics/`:

| Arquivo       | Conteúdo                                         |
|---------------|--------------------------------------------------|
| `metrics.db`  | Samples + top PIDs + process groups + Docker     |
| `alerts.log`  | Histórico de alertas                             |

Retenção (otimizada):

- raw (~30s): 24h (+ top 5 PIDs, top 15 grupos por nome, containers running)
- médias 5 min: 7 dias
- médias 1h: 30 dias

## Alertas

Config padrão em `~/.config/wslmetrics/config.yaml` (criado no primeiro `sample` / `install-job`):

```yaml
alerts:
  mem_percent: 90
  cpu_percent: 95
  docker_mem_bytes: null
  wsl_mem_percent: 90   # null desliga; usa memory= do .wslconfig
  cooldown_seconds: 300
sample_interval_seconds: 30
```

Quando um threshold é cruzado (respeitando cooldown):

1. Linha em `~/.local/state/wslmetrics/alerts.log`
2. Notificação via `notify-send` (WSLg), se disponível

### Limite WSL (`.wslconfig`)

O app lê `memory=` da seção `[wsl2]` em `%USERPROFILE%\.wslconfig` (via `/mnt/c/Users/...`).  
Se encontrar, a summary bar mostra `WSL used/limit` e o alerta `wsl_mem_percent` dispara perto do teto da VM.

## Export CSV

```bash
wslmetrics export-csv -o metrics.csv
wslmetrics export-csv --from 2026-10-09T00:00:00Z --to 2026-10-10T00:00:00Z
wslmetrics export-csv -o metrics.csv --include-processes
```

Colunas: `ts,iso,cpu,mem_percent,mem_used,mem_total,docker_mem`.  
Com `--include-processes`, também gera `*_processes.csv` (só janela raw de 24h).

## Notas WSL

- Métricas vêm do kernel Linux do WSL2 (`/proc`) — uso **dentro da distro**, não do Task Manager do Windows.
- Sem Docker acessível, a aba Containers mostra aviso; o restante continua normal.
- Templates systemd de referência: `packaging/systemd/`. O `install-job` gera units com o path absoluto do `wslmetrics` do seu venv.
