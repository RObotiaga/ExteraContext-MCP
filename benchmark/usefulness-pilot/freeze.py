"""Freeze once BEFORE MCP runs. Labels authored from wiki/source prose, not search output."""
import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = Path('/home/aptem/.hermes/cache/scratch/extera-install-work/knowledge-source')
CORPUS = Path('/home/aptem/.hermes/cache/scratch/extera-install-work/tooling-corpus/exteracontext.sqlite')
COMMIT = 'dfaba00ba86356ec46d376a2f425ccd29a62a654'
assert subprocess.check_output(['git','rev-parse','HEAD'], cwd=SOURCE, text=True).strip() == COMMIT
assert not (HERE / 'dataset.json').exists(), 'Never overwrite a frozen dataset'

def evidence(name, start, end):
    path = 'data/wiki/sources/' + name + '.md'
    raw = (SOURCE / path).read_bytes()
    return {'path':path, 'lines':[start,end], 'file_sha256':hashlib.sha256(raw).hexdigest(),
            'excerpt':'\n'.join(raw.decode().splitlines()[start-1:end]), 'level':'Knowledge-authored source synopsis; raw upstream capture absent'}

def case(id, family, split, prompt, query, groups, refs, must, hard):
    return dict(id=id, family=family, split=split, prompt=prompt, query=query, groups=groups,
                evidence=refs, must_handle=must, hard_fail=hard,
                target={'client':'ExteraGram','platform':'Android','client_version':'12.10.1','sdk_version':'1.4.5.5'},
                limit=6, runtime_status='NOT CLAIMED')

common = ['Claiming Android runtime PASS without execution', 'Equating docs/code with exact target-version compatibility']
cases = [
case('P01','outgoing','development','Implement a single-file plugin that rewrites only outgoing text starting with .hello to Hello; leave media and unrelated messages untouched. Target ExteraGram Android 12.10.1 / SDK 1.4.5.5. Provide code, citations and unverified assumptions.',
     'перехват исходящих сообщений изменение текста params.message',
     [['official-sdk:hook-registration'],['official-sdk:outgoing-hook'],['official-sdk:hook-strategies']],
     [evidence('official-sdk',46,84)], ['Register in load','Guard absent/non-string message','Return MODIFY with params only for matched text','Neutral HookResult otherwise'], common + ['Cancels or resends unrelated messages']),
case('P02','background-ui','development','Review a plugin that performs HTTP and JSON parsing inside a UI click callback then creates a dialog on the worker thread. Target ExteraGram Android 12.10.1 / SDK 1.4.5.5. Give a corrected scheduling sketch, source citations, error handling and lifecycle caveats.',
     'фоновая сеть обработка JSON очередь обновление интерфейса диалог',
     [['official-sdk:queue-api'],['official-sdk:ui-thread'],['official-sdk:dialogs']],
     [evidence('official-sdk',94,94),evidence('official-sdk',118,120)], ['Network off UI','Dialog creation/show on UI','Handle network failure','Suppress stale result after unload'], common + ['Blocking network on UI','Dialog creation on worker']),
case('P03','cleanup','development','A plugin owns a custom worker and an external NotificationCenter listener in addition to SDK Xposed hooks. Make unload/reload safe. Target ExteraGram Android 12.10.1 / SDK 1.4.5.5. Distinguish SDK-managed cleanup from plugin-owned cleanup; cite evidence and identify races.',
     'выгрузка плагина очистка worker listeners hooks reload',
     [['official-sdk:lifecycle'],['official-sdk:xposed-hooks']],
     [evidence('official-sdk',46,47),evidence('official-sdk',130,130)], ['Stop own workers','Remove external listeners','SDK automatic Xposed cleanup','Repeated load/unload safe','Late callbacks cannot mutate disposed UI'], common + ['Assuming SDK removes all external listeners']),
case('P04','settings-persistence','heldout','Implement a persistent enabled toggle that survives reload/update; explain when changing a setting needs settings-page reload. Target ExteraGram Android 12.10.1 / SDK 1.4.5.5. Use documented storage, code and citations.',
     'настройки сохранение переключатель reload update get_setting set_setting',
     [['official-sdk:settings-storage'],['official-sdk:settings-row-types'],['official-sdk:elyx-settings']],
     [evidence('official-sdk',112,114),evidence('official-sdk',196,196)], ['Stable key and default','Use plugin-scoped settings','Persistence statement limited to documentation','reload_settings for row structure changes'], common + ['Only in-memory global for persisted value']),
case('P05','account-request','heldout','A callback from background account 2 sends a TL request while account 0 is selected in UI. Show correct account routing and response/error handling. Target ExteraGram Android 12.10.1 / SDK 1.4.5.5. Cite source and note scope exceptions.',
     'запрос background account send_request callback response error',
     [['official-sdk:account-scope','official-sdk:account-client'],['official-sdk:send-request'],['official-sdk:notification-account']],
     [evidence('official-sdk',90,100)], ['Route to originating account','Callback response,error','Handle error before success','Notification delegate has no automatic scope'], common + ['Routes through UI-selected account without justification']),
case('P06','context-menu','heldout','Sketch adding a message-context menu action and removing it safely on unload. Target ExteraGram Android 12.10.1 / SDK 1.4.5.5. Identify callback context and distinguish third-party documentation examples from verified runtime.',
     'контекстное меню сообщения add_menu_item MenuItemData remove_menu_item',
     [['skill-faust:skill-faust-014']],
     [evidence('skill-faust',12,16),evidence('skill-faust',39,39)], ['MenuItemType.MESSAGE_CONTEXT_MENU','Preserve registration id','Callback receives context','Remove own item or document SDK cleanup','Secondary docs not runtime proof'], common + ['Invented callback signature presented as verified']),
case('P07','donor-api','heldout','Can a plugin directly rely on Nagram ObserversGroup.removeAllObservers on ExteraGram Android 12.10.1 / SDK 1.4.5.5? Explain the donor lifecycle pattern and a safe portability decision with citations.',
     'Nagram ObserversGroup removeAllObservers cleanup account',
     [['nagram:nagram-011']],
     [evidence('nagram',16,18),evidence('nagram',45,45),evidence('nagram',66,66)], ['Identify Nagram internal code','Explain group cleanup','ExteraGram availability unknown','Do not infer absence from empty results'], common + ['Promises donor class as supported ExteraGram SDK']),
case('P08','unknown-version','heldout','Assess whether send_request is proven compatible on ExteraGram Android 99.99.99 / SDK 99.99.99. Give documented API shape if evidence exists, but keep support unknown absent version-specific proof; propose concrete verification, not an unsupported incompatibility claim.',
     'send_request версия SDK совместимость',
     [['official-sdk:send-request'],['official-sdk:version-doc-skew','official-sdk:runtime-validation']],
     [evidence('official-sdk',13,15),evidence('official-sdk',100,100)], ['Known documented API shape','Unknown exact support','Separate documentation from runtime','Need actual target test/source proof'], common + ['Declares incompatible from no hit'])
]
cases[-1]['target'].update(client_version='99.99.99',sdk_version='99.99.99')
data = {'schema':'usefulness-pilot-1','status':'experimental bounded pilot, no significance claim',
        'knowledge_commit':COMMIT,'source_root':str(SOURCE),'corpus_path':str(CORPUS),
        'corpus_sha256':hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
        'label_provenance':'Independent manual labels from existing wiki/source prose; fact IDs mapped from source JSON BEFORE MCP baseline. Not primary upstream revalidation.',
        'leakage_boundary':'Development families P01-P03 only guide changes. Heldout P04-P08 frozen before baseline; family disjoint, shared SDK documents mean NOT independent corpora. Evaluator groups/excerpts withheld from agents. Author sees all labels: not evaluator-blind research.',
        'budgets':{'retrieval_limit':6,'max_tool_rounds':6,'max_wall_seconds':180,'model':'gpt-6.1-sol','provider':'openai-codex','reasoning':'medium'},
        'cases':cases}
raw = (json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode()
(HERE/'dataset.json').write_bytes(raw)
(HERE/'dataset.sha256').write_text(hashlib.sha256(raw).hexdigest()+'  dataset.json\n')
print('FROZEN', hashlib.sha256(raw).hexdigest(), 'cases',len(cases))
