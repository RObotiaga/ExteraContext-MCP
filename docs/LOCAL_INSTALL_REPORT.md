# Локальная готовая установка ExteraContext: результаты проверки

Статус: локальный installable artifact собран и проверен на настоящем корпусе.
Публичная дистрибуция/production promotion НЕ выполнены и НЕ разрешены этим отчётом.
Независимое ревью родительским агентом перед публикацией остаётся обязательным.

## Область изменений

Рабочая копия: `/home/aptem/.hermes/cache/scratch/exteracontext-review`
Ветка: `feature/release-readiness-fixes`
HEAD остался `6ac91416aa920309600ee11d89e3e81c957f8c3b`.
Изменения не закоммичены. Commit/push/merge/release/settings/production не менялись.
`KNOWLEDGE_LOCK` и `.github/workflows/sync-knowledge.yml` побайтно совпали с HEAD.
Делегирование не использовалось. Постоянная конфигурация/skills Hermes не менялись.

Файлы:

- `scripts/local_install.py`: offline user-owned installer/launcher, строгая проверка inventory,
  существующий corpus preflight, отдельный state, version switching, lock, rollback.
- `scripts/package_local.py`: локальный tarball с allowlist runtime, корпусом, manifest,
  npm-ci dependencies, BUNDLE inventory и внешним checksum; без публикации.
- `scripts/build_local_corpus.py`: exact-commit git archive, Docker build/validation
  без сети, remote builder не исполняется на host.
- `scripts/local_acceptance.mjs`: официальный SDK из самого артефакта, свежая установка
  одной командой, реальные запросы, restart/reinstall/update/rollback и отказы.
- `tests/test_local_install.py`: lifecycle/security regression, только disposable fixtures.
- `README.md`, `mcp/README.md`, `docs/LOCAL_INSTALL.md`: готовая установка и ограничения.
- `docs/LOCAL_INSTALL_REPORT.md`: этот фактический отчёт.

## Артефакт и идентичность

Артефакт:
`/home/aptem/.hermes/cache/scratch/extera-install-work/artifacts/exteracontext-0.7.0-local-6ac9141-p2.tar.gz`

Размер: 9390990 bytes.
SHA-256: `0939db631c66e1097e808b7518727e2c6e243345f0ba6336416799a91a29de0c`
Checksum: тот же абсолютный путь с суффиксом `.sha256`.
`sha256sum -c` реально вернул `ЦЕЛ`.

BUNDLE SHA-256 / release ID:
`2fafc592d7ac6956435f07b964a55613436de7cc2535e8eb75b8465acf0d3bcb`
Inventory: 1852 payload files. Native Node libraries отсутствуют.
Проверка archive inventory не обнаружила `.git`, `.env` или `default.session`.
Installer/runtime/schema внутри финального архива побайтно/по SHA совпали с текущими
файлами рабочей копии. HEAD описывает исходную базу; локальные review changes
идентифицируются installer bytes и BUNDLE hashes, а не новым commit.

Источник: публичный `https://github.com/RObotiaga/ExteraContext-Knowledge`.
Knowledge commit: `dfaba00ba86356ec46d376a2f425ccd29a62a654`.
SHA-256 git archive этого commit:
`2a29fc9fc524a31b527f2e068286b2dd402a513f69c1665b97523d8374a8248e`
SHA-256 builder `scripts/build_index.py`:
`147f7f4a06c5570269354b20f07f15ddcdc4a61cd870c7e5b9236835fbd97035`
SHA-256 `data/wiki/facts.json`:
`818be049016a8387aa830f4013013d73ed685575a9ee8b5ad6f86ce5690d30a3`
SHA-256 `data/legacy-source-runs.json`:
`39ebf715d158fce6edefeeac06b1dad6756f88626ae15683b9ad27bbcb370b38`
SHA-256 MCP `package-lock.json`:
`63ad8b0f3721f6e08daee7e3edb425b073c170eb19c30573370744add28a39c3`

Настоящий корпус:

- facts: 2189
- docs: 205
- source_runs: 174
- source_provenance: 76
- DB size: 17072128 bytes
- DB SHA-256: `199b5f4ad1563154607dcebb165b83431c4586aad66d35f569b4932b18373f59`
- Manifest SHA-256: `c0949583d8663eb0a08488d9a9b92994f2233b3f43e5f1e68a07930f37f6a150`

Builder предварительно прочитан. Финальный корпус создан самим новым helper в
Docker: network=none, UID 65534, read-only source/root, cap-drop ALL,
no-new-privileges, bounded resources. В build-контейнере только exported source и
candidate; .git/host credentials/environment не передавались. Валидатор запускался
в отдельном изолированном контейнере. Локальная image идентичность:
`sha256:c421fdf5ade7b1aef22883d5a40551c133d3fb6dffa0d51ba96157b446b884e7`.
Запись условий: `/home/aptem/.hermes/cache/scratch/extera-install-work/tooling-corpus/BUILD.json`.

Начальный запуск без SELinux workaround получил Permission denied; успешный build
helper и acceptance явно использовали `--security-opt label=disable` только для
этих disposable containers. Глобальная политика SELinux не менялась. Это ослабление
одного слоя изоляции задокументировано, а не скрыто.

## P2: сохранение пары current/previous при отказе переключения

Исправлен только независимый P2 из `extera-install-review.log`.
В этой итерации изменены `scripts/local_install.py`, `tests/test_local_install.py`
и этот отчёт. Предшествующие изменения рабочей копии сохранены без расширения scope.

`switch()` проверяет оба destination/staging entries и подготавливает оба symlink
до изменения указателей. Исходный previous заранее сохранён резервным symlink в
disposable `.pointers-*` directory. Если `os.replace(current)` бросает OSError,
previous восстанавливается через os.replace без нового выделения symlink;
если previous исходно отсутствовал, вновь созданный previous удаляется.
Созданные этой операцией staging entries очищаются; чужой stale entry не удаляется.
Install и rollback вызывают один и тот же helper для пары указателей.

Реальная файловая система, не модель в памяти: current=B, previous=A;
отказ install C и отказ rollback при stale `.current-new`, при ошибке создания
`.current-new` и при ошибке замены current сохраняют ОБА указателя.
После устранения отказа повторный rollback реально активирует A.
Для ошибок системных вызовов инъецируется только конкретный os.symlink/os.replace;
остальные операции, inventory, install, main и последующий rollback реальные.
Отдельно проверено восстановление отсутствующего previous при failed first update.
Tiny payload/query fixtures предназначены только для pointer regression,
не выдаются за реальный корпус или corpus acceptance.

Новые результаты в `/home/aptem/.hermes/cache/scratch/extera-install-work/p2/logs/`:

- `tdd-stale-red.log`: 2 ожидаемых FAIL (install/rollback теряли previous).
- `tdd-stale-green.log`: stale regression PASS после подготовки пары.
- `tdd-replace-red.log`: 2 ожидаемых FAIL при os.replace(current), preparation cases PASS.
- `local-installer-final.log`: 9 tests, OK, без skip.
- `offline-regression-final.log`: полный offline fixture-suite, 163 tests, OK; `ci-offline: ok`.
- `mcp-check.log`: npm run check PASS.
- `mcp-regression-corpus.log`: 30 tests, 30 PASS, 0 FAIL, 0 skipped.
- `mcp-regression.log`: первоначальный запуск без DB path честно упал (3 FAIL,
  2 skipped); повторный запуск выше использовал actual tooling-corpus и отдельный
  scratch overlay/run root. Никакой production DB не создавалась.
- `package.log`: package_local.py с `--corpus .../tooling-corpus`,
  `--dependencies .../dependency-build/node_modules` и новым `--output ...-p2.tar.gz`.
- `artifact-verification.json`: 1852 inventory files, безопасные уникальные archive
  entries; install.py побайтно совпадает с исправленным helper;
  DB и manifest побайтно совпадают с настоящим tooling-corpus.
  Installer SHA-256: `50685893f7ac31966a03c8a221fedf10782a4f9c950cf17fb19d4febc109913f`.

Новый artifact, а не предыдущий tarball, прошёл fresh clean acceptance:
`acceptance.log`, 32 стадии PASS, 21 wire response, 5 SDK sessions.
Контейнер `extera-local-prereqs:review`, image ID тот же, что ниже;
`--network none --read-only --cap-drop ALL --user 65534:65534`,
`--security-opt label=disable --security-opt no-new-privileges`, tmpfs /work и /tmp,
HOME=/work/home. Только artifact:ro, acceptance.mjs:ro и изначально пустой /out:rw.
Извлечение в /work/extracted и `node /acceptance.mjs`, exit 0.
Wire JSON: `/home/aptem/.hermes/cache/scratch/extera-install-work/p2/acceptance/`.
Предыдущий reviewed artifact не перезаписан, корпус не пересобирался и не изменялся.

Это обработка обычного одиночного отказа filesystem operation, не multi-file
power-loss transaction. Если сама recovery-операция тоже отказывает (например,
файловая система стала read-only), гарантировать восстановление нельзя: ошибка
не скрывается. Failed install может оставить проверенный неактивный release и
обновлённый launcher; гарантия этого P2 относится к current/previous, не к откату
всех install side effects. Не добавлялись новая архитектура или durable journal.
Commit/push/publication/settings/production не выполнялись; Knowledge LICENSE
остаётся блокером публичной дистрибуции. Parent review/re-run ещё предстоит.

## Предыдущие фактические RED / GREEN (до P2)

Все логи ниже в `/home/aptem/.hermes/cache/scratch/extera-install-work/logs/`.

- `tdd-01-red.log`: отсутствовал package_local.py — ожидаемый FAIL.
- `tdd-01-green.log`: install/reinstall/state preservation — PASS.
- `tdd-02-red.log`: отказ symlink launcher происходил ПОСЛЕ смены current — FAIL.
- `tdd-02-green.log`: исправлен порядок активации — PASS.
- `tdd-03-red.log`: nested BUNDLE exemption, Node/Python injection env,
  SQLite sidecar symlink — 3 ожидаемых FAIL; после исправлений включены в GREEN.
- `tdd-04-red.log`: отсутствовал isolated builder helper — FAIL.
- `tdd-04-green.log`: builder exact-commit refusal и предыдущие cases — PASS.
- `tdd-05-red.log`: fixture/build tooling попадали в runtime — FAIL.
- `tdd-05-green.log`: явный runtime allowlist — PASS.
- `tdd-06-red.log`: prerequisite Node probe исполнял NODE_OPTIONS — FAIL.
- `tdd-07-red.log`: failed rollback менял active release — FAIL;
  NODE_OPTIONS case в этом прогоне уже PASS.
- `tdd-final-green.log`: `Ran 6 tests in 9.546s`, `OK`, без skip.

Реальные промежуточные ошибки не подменялись правдоподобными ответами. Отдельно
исправлены harness assumptions о doctor envelope и lazy overlay initialization;
финальный harness использует настоящий response.data.index и штатную схему overlay.

## Предыдущий Regression (до P2)

- `python-regression-final.log`: `Ran 160 tests in 30.622s`, `OK`.
  Исходные 154 плюс 6 новых тестов; установленные MCP dependencies позволили
  выполнить optional startup cases без skip.
- `mcp-regression.log`: tests 30, pass 30, fail 0, skipped 0.
- `mcp-check.log`: npm run check PASS.
- `ci-offline-final.log`: отдельный старый fixture-based путь, 160 tests OK,
  `ci-offline: ok`. Это regression, НЕ подтверждение полезности реального корпуса.
- `smoke.py.log`: `smoke: ok` на настоящем корпусе.
- `test_skill_contract.py.log`, `test_knowledge_writeback.py.log`,
  `test_orchestrator.py.log`: standalone tests OK.
- `validate.py.log`: benchmark schema/packet validation PASS.
- `npm-ci.log`: locked install, `added 17 packages in 2s`, lifecycle scripts отключены.

При первом regression invocation глобальное REQUIRE_MANIFEST=1 намеренно сломало
старые unmanaged-fixture cases. Финальный обычный regression выполнен с 0, как
предусмотрено их контрактом; существующий manifest реального корпуса всё равно
проверяется. Managed installer/start и clean acceptance всегда принудительно 1.
Это не ослабление production preflight.

## Предыдущий Clean-install acceptance (до P2; новый прогон описан выше)

Реально свежий контейнер: Linux Alpine, Node v24.18.1, Python 3.14.7,
SQLite 3.53.4, FTS5 PASS. Generic OS prerequisites уже в локальной image; никаких
предварительных MCP dependencies, репозитория, DB или пользовательской EXTERACONTEXT
конфигурации в контейнере не было. Монтировались только final tarball, standalone
acceptance script и пустой result directory. HOME был новый /work/home; сеть отключена,
root filesystem read-only, UID 65534, capabilities отсутствовали.

Первый SDK transport запускал непосредственно `python3 install.py --start --modern-only`:
инсталляция и handshake одним вызовом, без ручных DB/env paths. Стандартное место
`/work/home/.local/share/exteracontext`, отдельные state/overlay и state/runs.
Дальнейшие сессии запускали установленный launcher.

Результат: 32 именованных стадии PASS, 5 SDK sessions, MCP 2026-07-28 и 14 tools.
Проверены first/restart/reinstall/update/rollback:

- initialize/list действительно состоялись с official SDK v2.1.0 из артефакта;
- doctor: 2189 facts, 205 docs, точный DB digest, manifest_status=verified;
- find_api(send_request): 6 реальных результатов, включая `official-sdk:send-request`;
- search_knowledge(send_request): найден тот же настоящий official fact;
- get_evidence: возвращает `https://plugins.exteragram.app/docs/client-utils`;
- реальный reflect_on_task создал сохранённый capture run;
- restart/reinstall сохранили overlay digest и run directory;
- malformed внутренний manifest даже с согласованным внешним BUNDLE отвергнут,
  current остался прежним;
- update/rollback сохранили overlay и реальную retrieval работоспособность;
- повреждения runtime VERSION и corpus manifest отвергнуты;
- base digest неизменен, overlay integrity_check=ok, local sentinel сохранён.

Overlay SHA-256 после initialization/sentinel и на restart/reinstall/update/rollback:
`9908b1f3a557c03dcc5ca097ba5cbbfe5f3dcf570c87ebebc51c892891ede619`.
Sentinel — тест пользовательского state, не corpus fallback и не новое знание.
Update изменял только локальный version marker и BUNDLE для проверки lifecycle;
межверсионные несовместимые schema migrations этим НЕ доказаны.
`commit_verified=false` остаётся честным: adjacent manifest подтверждает
consistency, а source commit установлен отдельной процедурой clone/archive/build.
Android runtime verification, agent quality или универсальная совместимость
целевых клиентов не заявляются.

Логи и wire responses:

- `/home/aptem/.hermes/cache/scratch/extera-install-work/logs/acceptance-final.log`
- `/home/aptem/.hermes/cache/scratch/extera-install-work/acceptance-final/acceptance-results.json`
- `/home/aptem/.hermes/cache/scratch/extera-install-work/acceptance-final/acceptance-trace.json`
- `/home/aptem/.hermes/cache/scratch/extera-install-work/logs/tooling-corpus.log`
- `/home/aptem/.hermes/cache/scratch/extera-install-work/logs/package-final.log`

Capability/token fields в wire trace отредактированы. Исходный synthetic E2E report
`/home/aptem/.hermes/cache/scratch/extera-tool-pipeline/REPORT.md` не переименован в
production-проверку; настоящая corpus acceptance находится отдельно выше.

## Ограничения и решение о публикации

Локальный проверенный offline install — готов. Public release — НЕ ГОТОВ К
ПУБЛИКАЦИИ без независимого parent review и существующего protected механизма.
AUTO_SYNC по legacy KNOWLEDGE_LOCK всё ещё недоступен/fail-closed; hashes и release
assets в upstream не создавались. GitHub permissions/environments/settings не менялись.

Node >=20 и Python >=3.11/FTS5 — явные prerequisites; минимальные заявленные версии
не тестировались отдельно (фактически проверены Node 24/Python 3.14). Автоматическая
установка через sudo/package manager намеренно отсутствует. Windows installer не
поддержан, macOS/другие архитектуры не проверены. HTTP сохраняет loopback + mandatory
bearer и не является zero-config remote deployment. Rollback не восстанавливает
overlay данные/будущие несовместимые миграции; нужны quiescent state backups.
Tarball не reproducible-bytes build, переключение не multi-file power-loss transaction.
Original raw remote source trees не входят в indexed corpus bundle.

У inspected Knowledge commit отсутствует top-level LICENSE; правомерность публичного
перераспространения корпуса/производных материалов требует отдельного review.
Хеш рядом с артефактом не заменяет аутентифицированный канал доверия. Никаких решений
о public distribution и production от лица parent reviewer этот отчёт не принимает.
