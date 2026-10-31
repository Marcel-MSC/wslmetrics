# wslmetrics

TUI no terminal para monitorar **CPU**, **RAM**, **swap**, **disk**, **load** e **rede** no WSL: top processos (agrupados ou por PID), detalhe/kill, containers Docker, diffs de RAM, histórico com gráficos, alertas e export CSV/JSON (+ textfile Prometheus).

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
# desenvolvimento editable (reflete src/ sem reinstall a cada edit):
pipx install -e /path/to/wslmetrics --force
# depois: feche a TUI (q) e abra de novo — processo antigo não recarrega código
```

Confira qual binário está no PATH (`which -a wslmetrics`): `~/.local/bin/wslmetrics` é o pipx; `.venv/bin/wslmetrics` só vale com o venv ativado. `pip install -e .` no venv **não** atualiza o pipx.

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
| `s`   | Persist ON/OFF — grava samples no SQLite (sem alertas) |
| `t`   | Alterar `top` na sessão                   |
| `i`   | Alterar `interval` (s) na sessão          |
| `/`   | Filtrar por nome (Processes + Containers) |
| `d`   | Detalhe do processo; `k` = SIGTERM (seu user), `K` = sudo kill (senha) |
| `Enter` / double-click | Copiar célula (Processes + Containers) |
| `Esc` | Fechar prompt / limpar filtro / fechar modal |

Com **GROUP ON**, aparece um badge amarelo e o subtítulo `GROUP ON` no header.  
Com **PERSIST ON**, grava no SQLite no máximo a cada `sample_interval_seconds` (config) — útil para History/Diffs sem o job systemd; **não** dispara alertas.  
Com alerta recente (dentro do cooldown), aparece o badge **ALERT**.

Abas:

- **Processes** — por PID ou agrupado (`g`); `d` abre detalhe
- **Containers** — sort `c`/`m` + filtro `/`
- **System** — load, CPU por core, mounts `/` e `/mnt/c`, I/O e rede (B/s)
- **Diffs** — crescimento de RAM de processos e containers (último sample + última 1h)
- **History** — gráficos de RAM%, CPU%, Docker mem, CPU queue (1m) e disk %
- **Alerts** — thresholds, cooldown e tail de `alerts.log`

## Histórico e jobs

O histórico é preenchido pelo job `systemd --user`, por `wslmetrics sample`, ou pela TUI com **PERSIST ON** (`s`).

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
| `metrics.prom`| Textfile Prometheus (atualizado a cada `sample`) |
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
  swap_percent: null    # null desliga; % de swap do host
  cooldown_seconds: 300
sample_interval_seconds: 30
```

Quando um threshold é cruzado (respeitando cooldown) no `sample` / job:

1. Linha em `~/.local/state/wslmetrics/alerts.log`
2. Notificação via `notify-send` (WSLg), se disponível

A aba **Alerts** só lê log/state (não avalia thresholds no refresh live).

### Limite WSL (`.wslconfig`)

O app lê `memory=` e `swap=` da seção `[wsl2]` em `%USERPROFILE%\.wslconfig` (via `/mnt/c/Users/...`).  
Se encontrar, a summary bar mostra uso vs limite WSL; o alerta `wsl_mem_percent` dispara perto do teto de RAM da VM. Swap real vem de `psutil`; o limite `swap=` do `.wslconfig` aparece quando disponível.

## Export CSV / JSON

```bash
wslmetrics export-csv -o metrics.csv
wslmetrics export-csv --from 2026-10-09T00:00:00Z --to 2026-10-10T00:00:00Z
wslmetrics export-csv -o metrics.csv --include-processes --include-containers --include-groups
wslmetrics export-json -o metrics.json --include-containers --include-groups
```

Samples: `ts,iso,cpu,mem_percent,mem_used,mem_total,docker_mem,cpu_queue_1m,disk_*,net_*`.  
Flags `--include-*` geram sidecars CSV (`*_processes.csv`, `*_containers.csv`, `*_groups.csv`) ou chaves extras no JSON (janela raw de 24h).

Cada `wslmetrics sample` (e persist da TUI) também grava `~/.local/state/wslmetrics/metrics.prom` para o textfile collector do node_exporter.

## Notas WSL

- Métricas vêm do kernel Linux do WSL2 (`/proc`) — uso **dentro da distro**, não do Task Manager do Windows.
- Sem Docker acessível, a aba Containers mostra aviso; o restante continua normal.
- Templates systemd de referência: `packaging/systemd/`. O `install-job` gera units com o path absoluto do `wslmetrics` do seu venv.
