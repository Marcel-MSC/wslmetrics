# Roadmap — wslmetrics

Ideias de evolução a partir do que já existe (TUI, sample/systemd, alertas, export CSV).

O núcleo atual cobre bem CPU/RAM/swap de host + Docker. Os itens abaixo são extensões naturais ou gaps observados no código.

---

## Alto valor (encaixa no produto atual)

1. **Aba Alerts** — ver últimos disparos, cooldown e status na TUI (hoje só `alerts.log` + `notify-send`).
2. **Gráfico de `docker_mem` no History** — o dado já está no SQLite e no CSV, mas não aparece nos charts.
3. **TUI opcionalmente gravando samples** — History/Diffs ficam vazios se o job não rodou; um toggle “persist while watching” fecha esse buraco.
4. **Swap + limite do `.wslconfig`** — ler `swap=` e mostrar uso real (hoje só `memory=`).
5. **Diffs de containers** — quem cresceu de RAM no Docker, no mesmo espírito dos diffs por processo.

---

## Médio valor (métricas WSL que faltam)

6. **Disk / I/O** — `/` e mounts sob `/mnt/c` (pressão típica no WSL).
7. **Load average + CPU por core**.
8. **Network** — bytes in/out da interface WSL.
9. **Detalhe de processo** — cmdline, tree, parent; opcionalmente kill com confirmação.
10. **Export mais completo** — containers/groups, JSON, e talvez Prometheus textfile para Grafana.

---

## UX / polish

11. **Help overlay** (`?`) e pause (`p`).
12. **Editar thresholds na TUI** (ou `wslmetrics config`).
13. **Fallback cron** quando não houver systemd.
14. **Badge/status de alertas** no header quando algo estiver acima do limite.

---

## Qualidade

15. **Testes** para `export`, `history`, `diffs`, `sample` e CLI — hoje a cobertura concentra em storage/collectors/alerts.

---

## Gaps técnicos observados (contexto)

Úteis ao priorizar implementação:

- ~~A TUI live **não grava** no SQLite~~ — resolvido com toggle `s` (PERSIST ON), só SQLite/throttled, sem alertas.
- Alertas só rodam no pipeline de sample, não no refresh da TUI (aba Alerts é somente leitura).
- ~~`docker_mem` é persistido e exportado, mas não plotado~~ — History inclui gráfico de Docker mem.
- `.wslconfig`: `memory=` e `swap=` são lidos; `processors=` ainda não.
- Export CSV não cobre `containers` / `process_groups`.
- Diffs cobrem RSS por processo e mem por container (não PID, não CPU growth).
- Sem instalador de job além de systemd `--user` (README menciona cron manual).

---

## Próximo ciclo sugerido

Prioridade após o ciclo de alto valor:

1. Help overlay + pause  
2. Disk / I/O (pressão típica no WSL)  
3. Export containers/groups (CSV/JSON)  

---

## Status

| # | Item | Status |
|---|------|--------|
| 1 | Aba Alerts | feito |
| 2 | Gráfico `docker_mem` | feito |
| 3 | TUI persistindo samples | feito |
| 4 | Swap + `.wslconfig` | feito |
| 5 | Diffs de containers | feito |
| 6 | Disk / I/O | pendente |
| 7 | Load + CPU por core | pendente |
| 8 | Network | pendente |
| 9 | Detalhe de processo | pendente |
| 10 | Export completo / JSON / Prometheus | pendente |
| 11 | Help + pause | pendente |
| 12 | Editar thresholds | pendente |
| 13 | Fallback cron | pendente |
| 14 | Badge de alertas no header | feito |
| 15 | Testes (export, history, diffs, sample, CLI) | parcial |
