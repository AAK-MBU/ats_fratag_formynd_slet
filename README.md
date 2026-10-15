# ats_fratag_formynd_slet

ATS-proces der hver nat sletter gamle sager om fratagelse af forældremyndighed fra databaseskemaet `[RPA].[formynd]` på Automation Server (ATS). En sag er gammel, når den er lukket (`ErAktiv = 0`), og dens `SlutDato` ligger mere end 30 dage tilbage. Sager der er blevet for gamle, mens processen ikke har kørt, kommer med ved næste kørsel.

Sagerne oprettes og lukkes af [ats_fratag_formynd](https://github.com/AAK-MBU/ats_fratag_formynd). Lukker den en sag før tid, sætter den `SlutDato` til lukkedagen, så reglen gælder både for udløbne og for tidligt lukkede sager.

Processen sletter kun databaserækker. ESDH-sagen i GetOrganized røres ikke.

Processen bygger på [ats-process-framework](https://github.com/AAK-MBU/ats-process-framework).

## Flow

```
--queue    SELECT formynd.Sag WHERE SlutDato < frist
             DRY_RUN:  ──► dryrun_<dato>                    (ét rapport-item)
             ellers:   ──► slet_sag_<SagId>  pr. lukket sag
                       ──► ikke_lukket_<dato>  hvis udløbne sager stadig er aktive
                                   │
                                   ▼  processens egen kø
--process  slet_sag:     sletter sagens rækker i én transaktion
           dry_run:      mail med de sager, der ville blive slettet (+ ikke lukkede)
           ikke_lukket:  mail med udløbne sager, der ikke er lukket
```

`frist` er dags dato i Europe/Copenhagen minus `SLETTE_DAGE`.

| Trin | Flag | Rolle |
|---|---|---|
| Populate | `--queue` | `processes/populate.collect_items`: finder de udløbne sager og bygger items. References der allerede findes i køen, springes over. |
| Process | `--process` | `processes/handle_item.handle_item`: sletter eller mailer pr. item |
| Finalize | `--finalize` | no-op |

Processen er tænkt kørt med `--queue --process` én gang i døgnet.

### Dry-run
`DRY_RUN = True` er standard. Så sletter processen intet. Populate lægger ét item `dryrun_<dato>` i køen, når der er noget at rapportere, og behandlingen af det sender én mail med:
- de sager, en rigtig kørsel ville slette,
- de udløbne sager, der ikke er lukket.

Der oprettes ingen `slet_sag_`-items i dry-run, så de er ikke brugt op, når sletningen slås til.

### Sletning
Med `DRY_RUN = False` bliver hver kandidat til et item `slet_sag_<SagId>`. Behandlingen (`helpers/formynd_db.delete_sag`) sletter sagen i én transaktion:

1. Låser sagens række (`UPDLOCK, HOLDLOCK`) og tjekker reglen igen: `ErAktiv = 0` og `SlutDato < frist`. Er sagen forlænget eller genåbnet siden populate, slettes den ikke.
2. Sletter sagens rækker i `SagPartSvar`, `SagNotifikation`, `SagESDHReference` og `ProcesEventLog`, og til sidst i `Sag`.
3. Skriver en slettelog-række i `ProcesEventLog` med `EventType = 'SLETNING'`, `EventAction = 'SAG_SLETTET'`, sagens id og en payload med sagstype, slutdato, frist og antal slettede rækker pr. tabel. Rækken indeholder ingen personoplysninger.

Fejler et trin, rulles hele transaktionen tilbage. Får en ny tabel en fremmednøgle til `Sag` uden at blive tilføjet til `CHILD_TABLES`, fejler sletningen derfor uden at efterlade en halvt slettet sag.

| Udfald | Item'et |
|---|---|
| `slettet` / `allerede_slettet` | `completed`. Beskeden indeholder antal databaserækker pr. tabel og slettelog-rækkens `EventId`. |
| Sagen er aktiv, ikke udløbet længe nok, eller findes hverken i `Sag` eller slettelogen | `pending user action` uden fejlmail |
| Andre fejl | `failed` med fejlmail til `Error Email` |

Kaldet kan gentages. Findes sagen ikke, men står i slettelogen, giver det `allerede_slettet`.

En reference oprettes kun én gang. Et `slet_sag_`-item, der er endt som `failed` eller `pending user action`, bliver derfor ikke lagt i køen igen ved næste kørsel. Det skal sættes til `new` i ATS. Alternativt kan køen tilføjes `MONITORED_QUEUES` i [ats_fratag_formynd_alarm](https://github.com/AAK-MBU/ats_fratag_formynd_alarm), som så genkører items, der er gået i stå.

### Udløbne sager, der ikke er lukket
Sager hvis `SlutDato` ligger før `frist`, men som stadig har `ErAktiv = 1`, slettes ikke. Det er sager, hvor luk-subprocessen i ats_fratag_formynd ikke er gennemført, fx fordi ESDH-lukningen fejlede. I dry-run står de i dry-run-mailen. Ellers giver de ét item `ikke_lukket_<dato>` pr. dag, og behandlingen af det sender en mail med listen.

## Kør

Kræver Python 3.13 og [uv](https://docs.astral.sh/uv/). `DBCONNECTIONSTRINGPROD` bruges gennem SQLAlchemy og pyodbc og kræver en ODBC-driver til SQL Server. Værdien må være en rå ODBC-streng (`Driver={...};Server=...`) eller en SQLAlchemy-URL.

```sh
uv sync
cp .env.example .env                     # udfyld værdierne
uv run python main.py --queue --process
```

## Konfiguration

### `ats_framework/processes/slet_config.py`

| Navn | Betydning |
|---|---|
| `PROCESS_NAME` | Processens navn |
| `DRY_RUN` | `True`: kun rapport-mail, ingen sletning |
| `SLETTE_DAGE` | Antal dage efter `SlutDato`, en lukket sag gemmes |
| `TIMEZONE` | Tidszonen dags dato beregnes i |
| `SOURCE_COMPONENT` | `SourceComponent` og `CreatedBy` på slettelog-rækken |
| `SUBJECT_PREFIX`, `SUBJECTS` | Emnelinjer i rapport-mails |
| `RECIPIENTS_CONSTANT`, `SENDER_CONSTANT`, `SMTP_SERVER_CONSTANT`, `SMTP_PORT_CONSTANT` | Navne på konstanter i rpa.Constants |

### Miljøvariabler

| Variabel | Påkrævet | Betydning |
|---|---|---|
| `ATS_URL`, `ATS_TOKEN` | ja | ATS-API'et |
| `ATS_WORKQUEUE_OVERRIDE` | lokalt | Id på processens kø. I drift vælges den af ATS-sessionen. |
| `DBCONNECTIONSTRINGPROD` | ja | ODBC-forbindelse til RPA-databasen (konstanter og skemaet `formynd`) |
| `AARHUS_ROOT_CERT_PEM` | nej | PEM med ATS' CA. Tilføjes til de offentlige rodcertifikater. |
| `LOCAL_DEVELOPMENT` | nej | `true` sender alle rapport-mails til `TestEmail` |
| `TestEmail` | når `LOCAL_DEVELOPMENT=true` | Modtager af omdirigerede mails |

### RPA-databasen
- **Rettigheder:** kontoen i `DBCONNECTIONSTRINGPROD` skal have `SELECT`, `INSERT` og `DELETE` på skemaet `formynd`. ats_fratag_formynd selv kræver ikke `DELETE`, så den skal typisk tildeles særskilt.
- **Konstanter til rapport-mails:**
  - `rpa_team_email`: modtagere, som JSON-liste eller kommasepareret
  - `e-mail_noreply`
  - `smtp_adm_server`
  - `smtp_port`
- **Konstanter til frameworkets fejlmail:** `Error Email`, `Email Friend`, `smtp_server`.

## Datakilder

| Kilde | Adgang |
|---|---|
| `[formynd].[Sag]` | læser `SagId`, `SagType`, `SlutDato`, `ErAktiv`; sletter sagen |
| `[formynd].[SagPartSvar]`, `[SagNotifikation]`, `[SagESDHReference]` | sletter sagens rækker |
| `[formynd].[ProcesEventLog]` | sletter sagens rækker; skriver og læser slettelog-rækker |
| `[rpa].[Constants]` via `mbu_rpa_core.RPAConnection` | mail-opsætning |
| ATS `GET /workqueues/{id}/items` | læser køens referencer, så eksisterende items ikke oprettes igen |

## Projektstruktur

```
main.py                        entrypoint: --queue / --process / --finalize
ats_framework/core/            frameworkets løkker; delegerer til processes/
ats_framework/helpers/         ATS-kald, ORM-modeller og -forespørgsler, mail, TLS
ats_framework/processes/       slet_config, populate, handle_item
tests/                         pytest
```

## Test

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Testene kører isoleret. Database- og SMTP-kald monkeypatches; `formynd_db` testes mod en in-memory SQLite-database med ORM-modellerne, der kontrollerer hvilke rækker der slettes, slettelog-rækken og at intet ændres ved afvisning eller fejl. CI kører ruff. PR'er til `main` gates af **version-gate** (`.github/workflows/version-gate.yml`): er `version` i `pyproject.toml` ikke højere end på `main`, fejler checket og beder i en PR-kommentar om én label, `major`, `minor` eller `bugfix`. CI skriver så den nye version i `pyproject.toml` og pusher den til PR'ens branch.
