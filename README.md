# 🧬 Urology Intelligence

**Plataforma autônoma de inteligência científica** para Andrologia, Medicina Sexual,
Infertilidade e Reprodução Masculina (com cobertura secundária de HPB, câncer de
próstata e uro-oncologia).

Toda semana o sistema, de forma autônoma:

1. Busca novos artigos científicos (PubMed, CrossRef, Semantic Scholar, OpenAlex)
2. Faz triagem de relevância com LLM
3. Baixa PDFs open-access
4. Lê o texto completo e extrai uma análise estruturada
5. Faz apreciação crítica das evidências (risco de viés, qualidade, poder)
6. Traduz para a prática clínica ("o que muda amanhã?")
7. Monitora guidelines (AUA, EAU, ASRM, ISSM, ESHRE)
8. Compara cada estudo com as guidelines vigentes
9. Identifica oportunidades de pesquisa (revisão, meta-análise, mestrado, doutorado)
10. Aprende continuamente com o seu comportamento (feedback persistente)
11. Constrói uma base de conhecimento vetorial (RAG/Qdrant)
12. Gera a newsletter **Urology Intelligence Weekly** (Markdown + HTML + PDF)
13. Envia por **email** (SMTP/Gmail) — toda sexta-feira às 18:00 (America/Sao_Paulo); WhatsApp fica como canal opcional

---

## 🏗️ Arquitetura

```
                        ┌──────────────────────────────────────────────┐
                        │              LangGraph Pipeline               │
  PubMed ─┐             │  journal_scout → scientific_triage →          │
  CrossRef ┼─ fetch ──▶ │  pdf_collector → full_text_analyzer →         │ ─▶ PostgreSQL
  S2 ──────┤            │  evidence_appraiser → clinical_translator →   │ ─▶ Qdrant (RAG)
  OpenAlex ┘            │  guideline_tracker → guideline_comparator →   │ ─▶ storage/ (PDF, MD/HTML/PDF)
                        │  research_scout → personalized_learning →     │
  AUA/EAU/ASRM/ISSM ──▶ │  newsletter_editor → newsletter_dispatcher    │ ─▶ Email (SMTP) / WhatsApp
  ESHRE (guidelines)    └──────────────────────────────────────────────┘
                                        ▲ APScheduler (sex 18:00)
   FastAPI: /health /metrics /run /rag/query /feedback /newsletters/latest
   Observabilidade: Prometheus + Grafana
```

### Stack
Python 3.12 · LangGraph · OpenAI + Anthropic Claude · `text-embedding-3-large` ·
PostgreSQL + SQLAlchemy + Alembic · Qdrant · Redis · PyMuPDF · APScheduler ·
Structlog · Prometheus + Grafana · Docker / Docker Compose · Pytest.

---

## 📂 Estrutura de diretórios

```
urology-intelligence/
├── docker-compose.yml          # app, postgres, qdrant, redis, prometheus, grafana
├── Dockerfile
├── requirements.txt
├── pyproject.toml              # pytest + coverage config
├── alembic.ini
├── .env.example
├── app/
│   ├── main.py                 # FastAPI (API + lifespan que inicia o scheduler)
│   ├── config.py               # Settings (pydantic-settings) + journals/priorities
│   ├── scheduler.py            # APScheduler (sex 18:00 America/Sao_Paulo)
│   ├── cli.py                  # CLI: run / ask / init-db / serve
│   ├── database/               # base, models (11 entidades), session, migrations/
│   ├── agents/                 # 12 agentes LangGraph + common.py
│   ├── workflows/              # state.py (TypedDict) + weekly_pipeline.py (grafo)
│   ├── rag/                    # embeddings, vector_store (Qdrant), chunker, retriever
│   ├── integrations/           # pubmed, crossref, semantic_scholar, openalex,
│   │                           # unpaywall, whatsapp, aua/eau/asrm/issm/eshre
│   ├── prompts/                # triage, appraisal, clinical_translation,
│   │                           # guideline_comparison, newsletter
│   └── utils/                  # logger, metrics, retry, circuit_breaker, pdf_parser, llm
├── monitoring/
│   ├── prometheus/prometheus.yml
│   └── grafana/                # provisioning (datasource + dashboards) + dashboard JSON
├── storage/                    # pdfs/ newsletters/ cache/ clinical_knowledge/
└── tests/                      # suíte Pytest (mock de APIs externas)
```

---

## ⚙️ Pré-requisitos

- **Docker** + **Docker Compose v2** (modo recomendado — não precisa de Python local)
- Chaves de API:
  - `OPENAI_API_KEY` (embeddings + triagem)
  - `ANTHROPIC_API_KEY` (análise, apreciação, newsletter)
  - **Email (SMTP)** — canal padrão: `SMTP_USER` + `SMTP_PASSWORD` (no Gmail, gere uma *app password*). WhatsApp é opcional via `WHATSAPP_*`.
  - Opcionais (melhoram rate limits): `NCBI_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY`

---

## 🚀 Instalação e execução (Docker)

```bash
# 1) Configure o ambiente
cp .env.example .env
#    edite .env e preencha as chaves de API e a senha do Postgres

# 2) Suba toda a stack (app roda migrations automaticamente no boot)
docker compose up -d --build

# 3) Verifique a saúde
curl http://localhost:8010/health
```

Serviços expostos:

| Serviço      | URL                          | Observação                       |
|--------------|------------------------------|----------------------------------|
| API          | http://localhost:8010        | `/docs` para o Swagger           |
| Métricas     | http://localhost:8010/metrics| Prometheus scrape                |
| Prometheus   | http://localhost:9090        |                                  |
| Grafana      | http://localhost:3000        | login `admin` / `admin` (.env)   |
| Qdrant       | http://localhost:6333        | dashboard em `/dashboard`        |
| PostgreSQL   | localhost:5432               |                                  |

### Disparar uma execução manual (sem esperar a sexta-feira)

```bash
# Via API (assíncrono — retorna um run_id)
curl -X POST http://localhost:8010/run

# Ou via CLI dentro do container (síncrono — útil para logs/depuração)
docker compose exec app python -m app.cli run
```

A newsletter gerada fica em `storage/newsletters/newsletter_<run_id>.{md,html,pdf}`
e também em `GET /newsletters/latest`.

---

## 🔎 Base de conhecimento (RAG)

Pergunte em linguagem natural à base indexada (artigos, guidelines, newsletters):

```bash
curl -X POST http://localhost:8010/rag/query \
  -H "Content-Type: application/json" \
  -d '{"question": "Quais evidências sustentam TRT em hipogonadismo funcional?"}'

# Exemplos sugeridos:
# "Quais RCTs compararam clomifeno versus testosterona?"
# "Quais estudos sustentam micro-TESE em azoospermia não obstrutiva?"
# "Mudanças na guideline EAU de infertilidade masculina nos últimos anos?"
```

Ou via CLI:

```bash
docker compose exec app python -m app.cli ask "Evidências de micro-TESE em NOA?"
```

---

## 🧠 Aprendizado personalizado (feedback)

O agente 10 ajusta os pesos de relevância por tema a partir do seu comportamento.
Registre eventos (abertura, ignorado, curtido, salvo, tempo de leitura, nota):

```bash
curl -X POST http://localhost:8010/feedback \
  -H "Content-Type: application/json" \
  -d '{"event_type": "liked", "topic": "andrology", "article_id": 12, "read_seconds": 240}'
```

`event_type ∈ {opened, ignored, liked, disliked, saved}`. Os pesos são persistidos em
`topic_weights` e aplicados na triagem das próximas semanas.

---

## ⏰ Agendamento

O job semanal é registrado pelo APScheduler quando a API sobe (lifespan).
Padrão: **sexta-feira, 18:00, America/Sao_Paulo**. Configure em `.env`:

```env
PIPELINE_CRON_DAY_OF_WEEK=fri
PIPELINE_CRON_HOUR=18
PIPELINE_CRON_MINUTE=0
TIMEZONE=America/Sao_Paulo
```

---

## 📊 Monitoramento (Prometheus + Grafana)

- Métricas customizadas expostas em `/metrics`: execuções e duração do pipeline,
  duração por agente, artigos coletados/selecionados, PDFs, atualizações de
  guidelines, tokens de LLM, requisições externas, estado dos circuit breakers.
- O Grafana já vem **provisionado**: datasource Prometheus + dashboard
  *Urology Intelligence* (pasta `monitoring/grafana`). Abra http://localhost:3000.

---

## 🗄️ Banco de dados, migrations e backup

```bash
# Migrations (executadas automaticamente no boot do container app)
docker compose exec app alembic upgrade head
docker compose exec app alembic revision --autogenerate -m "minha mudança"

# Backup do PostgreSQL
docker compose exec postgres pg_dump -U urology urology > backup_$(date +%F).sql

# Restore
cat backup_2026-06-15.sql | docker compose exec -T postgres psql -U urology urology

# Backup do Qdrant e dos artefatos: basta versionar os volumes/ a pasta storage/
docker run --rm -v urology-intelligence_qdrantdata:/data -v $PWD:/backup alpine \
  tar czf /backup/qdrant_backup.tgz -C /data .
```

Entidades principais: `Article`, `Author`, `Journal`, `Analysis`,
`ClinicalInterpretation`, `Guideline`, `Newsletter`, `Embedding`, `UserFeedback`,
`TopicWeight`, `AuditLog`.

---

## 🧪 Testes

A suíte usa SQLite (modelos com tipo JSON portável) e **mocka todas as APIs
externas** (respx para HTTP, monkeypatch para LLM). Meta de cobertura: **80%**.

```bash
# Dentro do container
docker compose exec app pytest

# Ou localmente (com um venv e requirements instalados)
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -r requirements.txt
pytest
```

> O gate de cobertura (`--cov-fail-under=80`) considera a suíte completa. Código
> que só executa contra serviços ao vivo (chamadas reais de OpenAI/Anthropic/Qdrant,
> download de PDF, scraping de guidelines) é marcado com `# pragma: no cover`.

---

## 🛠️ Troubleshooting

| Sintoma | Causa provável | Ação |
|--------|----------------|------|
| `/health` mostra `db: down` | Postgres ainda subindo | aguarde o healthcheck; veja `docker compose logs postgres` |
| `qdrant: down` | Qdrant iniciando | aguarde; a coleção é criada sob demanda |
| Pipeline sem artigos | Janela de 7 dias sem publicações novas / rate limit | configure `NCBI_API_KEY`; ajuste `LOOKBACK_DAYS` |
| PDFs não baixados | Artigos sem versão open-access | normal; a análise cai no abstract |
| Email não envia | `SMTP_PASSWORD` ausente/incorreta | gere uma *app password* do Gmail e ponha em `SMTP_PASSWORD` (envio é pulado se não configurado) |
| `ui_app` não sobe: `Bind for 0.0.0.0:8000 failed: port is already allocated` | porta presa pelo proxy do Docker no Windows | a API é publicada no host em **8010** (ver `docker-compose.yml`); se precisar mudar, edite o mapeamento `8010:8000` |
| `pdf_render_failed` | libs nativas do WeasyPrint | ok no Docker (já instaladas); local pode gerar só MD/HTML |
| Erro de LLM/JSON | resposta malformada | há retry/backoff; veja `audit_logs` e logs estruturados |

Logs são estruturados (JSON em produção) e correlacionados por `run_id`/`agent`.
A trilha completa de cada execução fica na tabela `audit_logs`.

---

## 🔄 Atualizações

```bash
git pull
docker compose build app
docker compose up -d
docker compose exec app alembic upgrade head
```

---

## 🔐 Segurança

- Nenhum segredo é hardcoded — tudo vem do `.env` (não versionado; veja `.gitignore`).
- Resiliência: timeout, retry com backoff + jitter, rate-limit cortês e
  **circuit breaker** por serviço externo.
- Usuário não-root no container.

---

## 🔭 Visão de longo prazo

A arquitetura é modular para suportar, sem refatoração significativa:
dashboard web · app mobile · chat conversacional · geração automática de
PowerPoint / Journal Club / revisão narrativa · suporte a revisão sistemática ·
protocolos de pesquisa · assistente científico pessoal de urologia.

---

_Gerado para uso clínico-científico pessoal. Não substitui julgamento clínico nem
constitui recomendação formal de conduta._

