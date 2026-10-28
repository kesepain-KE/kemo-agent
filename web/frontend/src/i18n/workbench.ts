/** Right-side workspace chrome. User files and terminal output remain verbatim. */
export const workbenchMessages = `
工作面板|Workspace panel|Panel de trabajo|Panneau de travail|Arbeitsbereich|Painel de trabalho|Рабочая панель|作業パネル|작업 패널
工具装载的媒体|Tool-attached media|Medios adjuntados por herramientas|Médias joints par les outils|Per Tool geladene Medien|Mídia anexada por ferramentas|Медиа из инструментов|ツールで読み込んだメディア|도구로 첨부한 미디어
媒体已排队|Media queued|Medios en cola|Médias en attente|Medien eingereiht|Mídia na fila|Медиа в очереди|メディア待機中|미디어 대기 중
媒体已准备|Media prepared|Medios preparados|Médias préparés|Medien vorbereitet|Mídia preparada|Медиа подготовлены|メディア準備済み|미디어 준비됨
媒体已随请求发送|Media sent with request|Medios enviados con la solicitud|Médias envoyés avec la requête|Medien mit Anfrage gesendet|Mídia enviada com a solicitação|Медиа отправлены с запросом|リクエストとともに送信済み|요청과 함께 미디어 전송됨
媒体装载失败|Media attachment failed|Error al adjuntar medios|Échec du chargement des médias|Medien konnten nicht geladen werden|Falha ao anexar mídia|Ошибка загрузки медиа|メディアの読み込み失敗|미디어 첨부 실패
本轮未发送|Not sent in this run|No enviado en esta ejecución|Non envoyé durant cette exécution|In diesem Lauf nicht gesendet|Não enviado nesta execução|Не отправлено в этом запуске|この実行では未送信|이번 실행에서 전송되지 않음
发送状态未确认|Delivery not confirmed|Entrega no confirmada|Envoi non confirmé|Zustellung nicht bestätigt|Envio não confirmado|Отправка не подтверждена|送信状態未確認|전송 상태 미확인
原文件不可预览|Original file unavailable for preview|Vista previa del original no disponible|Aperçu du fichier original indisponible|Originaldatei nicht für Vorschau verfügbar|Arquivo original indisponível para prévia|Предпросмотр исходного файла недоступен|元ファイルはプレビューできません|원본 파일 미리보기 불가
携带媒体的请求已获得完整响应，不代表模型已正确理解。|The media-bearing request received a complete response; this does not prove correct understanding.|La solicitud con medios recibió una respuesta completa; no garantiza una comprensión correcta.|La requête contenant les médias a reçu une réponse complète, sans garantir leur bonne compréhension.|Die Anfrage mit Medien hat eine vollständige Antwort erhalten; das beweist kein korrektes Verständnis.|A solicitação com mídia recebeu uma resposta completa; isso não comprova compreensão correta.|Запрос с медиа получил полный ответ; это не гарантирует правильного понимания.|メディア付きリクエストは完全な応答を受信しましたが、正しい理解を保証するものではありません。|미디어가 포함된 요청이 완전한 응답을 받았으며 올바른 이해를 보장하지는 않습니다.
仅在本轮请求中装载；预览不会触发模型请求。|Attached for this run only; previewing does not call the model.|Se adjunta solo a esta ejecución; la vista previa no llama al modelo.|Chargé uniquement pour cette exécution ; l’aperçu n’appelle pas le modèle.|Nur für diesen Lauf geladen; die Vorschau ruft kein Modell auf.|Anexada apenas nesta execução; a prévia não chama o modelo.|Загружено только для этого запуска; предпросмотр не вызывает модель.|この実行のみ読み込みます。プレビューではモデルを呼び出しません。|이번 실행에만 첨부되며 미리보기는 모델을 호출하지 않습니다.
已复制|Copied|Copiado|Copié|Kopiert|Copiado|Скопировано|コピーしました|복사됨
复制失败|Copy failed|Error al copiar|Échec de la copie|Kopieren fehlgeschlagen|Falha ao copiar|Не удалось скопировать|コピーに失敗しました|복사 실패
终端输出|Console output|Salida de la consola|Sortie de la console|Konsolenausgabe|Saída do console|Вывод консоли|コンソール出力|콘솔 출력
跟随最新输出|Follow latest output|Seguir la salida más reciente|Suivre la dernière sortie|Neuester Ausgabe folgen|Acompanhar saída recente|Следить за новым выводом|最新の出力に追従|최신 출력 따라가기
已中断|Interrupted|Interrumpido|Interrompu|Unterbrochen|Interrompido|Прервано|中断|중단됨
正在启动|Starting|Iniciando|Démarrage|Wird gestartet|Iniciando|Запуск|起動中|시작 중
每个输出流最多显示最近 64 KiB、1000 行|Each stream shows up to the latest 64 KiB and 1,000 lines|Cada flujo muestra hasta los últimos 64 KiB y 1000 líneas|Chaque flux affiche au plus les derniers 64 Kio et 1 000 lignes|Je Stream werden bis zu 64 KiB und 1.000 letzte Zeilen angezeigt|Cada fluxo mostra até os últimos 64 KiB e 1.000 linhas|Для каждого потока показаны последние 64 КиБ и не более 1000 строк|各ストリームの直近 64 KiB、最大 1000 行を表示|각 스트림은 최근 64 KiB 및 최대 1,000줄 표시
Shell 后台任务|Shell background jobs|Tareas de Shell en segundo plano|Tâches Shell en arrière-plan|Shell-Hintergrundaufgaben|Tarefas Shell em segundo plano|Фоновые задачи Shell|Shell バックグラウンドタスク|Shell 백그라운드 작업
实例终端|Instance console|Consola de la instancia|Console de l’instance|Instanzkonsole|Console da instância|Консоль экземпляра|インスタンスコンソール|인스턴스 콘솔
调整工作面板宽度|Resize workspace panel|Cambiar ancho del panel|Redimensionner le panneau|Panelbreite ändern|Redimensionar painel|Изменить ширину панели|パネル幅を調整|패널 너비 조절
还原面板|Restore panel|Restaurar panel|Restaurer le panneau|Panel wiederherstellen|Restaurar painel|Восстановить панель|パネルを元に戻す|패널 복원
最大化面板|Maximize panel|Maximizar panel|Agrandir le panneau|Panel maximieren|Maximizar painel|Развернуть панель|パネルを最大化|패널 최대화
收起工作面板|Collapse workspace panel|Contraer panel|Replier le panneau|Panel einklappen|Recolher painel|Свернуть панель|作業パネルを閉じる|작업 패널 접기
工作面板标签|Workspace tabs|Pestañas de trabajo|Onglets de travail|Arbeitsbereich-Tabs|Abas de trabalho|Вкладки рабочей панели|作業パネルのタブ|작업 패널 탭
取消固定标签|Unpin tab|Desfijar pestaña|Détacher l’onglet|Tab lösen|Desafixar aba|Открепить вкладку|タブの固定を解除|탭 고정 해제
固定标签|Pin tab|Fijar pestaña|Épingler l’onglet|Tab anheften|Fixar aba|Закрепить вкладку|タブを固定|탭 고정
关闭标签|Close tab|Cerrar pestaña|Fermer l’onglet|Tab schließen|Fechar aba|Закрыть вкладку|タブを閉じる|탭 닫기
在工作面板打开|Open in workspace|Abrir en el panel|Ouvrir dans le panneau|Im Arbeitsbereich öffnen|Abrir no painel|Открыть в рабочей панели|作業パネルで開く|작업 패널에서 열기
查看后台任务|View background job|Ver tarea en segundo plano|Voir la tâche en arrière-plan|Hintergrundaufgabe ansehen|Ver tarefa em segundo plano|Посмотреть фоновую задачу|バックグラウンドタスクを表示|백그라운드 작업 보기
后台任务范围|Background job scope|Ámbito de tareas|Périmètre des tâches|Aufgabenbereich|Escopo das tarefas|Область фоновых задач|タスクの表示範囲|백그라운드 작업 범위
此用户全部任务|All jobs for this user|Todas las tareas del usuario|Toutes les tâches de cet utilisateur|Alle Aufgaben dieses Benutzers|Todas as tarefas deste usuário|Все задачи пользователя|このユーザーの全タスク|이 사용자의 모든 작업
当前对话任务|Current conversation jobs|Tareas de esta conversación|Tâches de cette conversation|Aufgaben dieses Gesprächs|Tarefas desta conversa|Задачи текущего диалога|現在の会話のタスク|현재 대화 작업
刷新任务|Refresh tasks|Actualizar tareas|Actualiser les tâches|Aufgaben aktualisieren|Atualizar tarefas|Обновить задачи|タスクを更新|작업 새로고침
仅查看受管理 Shell 后台作业，关闭标签不会停止任务。|View managed Shell jobs. Closing a tab does not stop the job.|Vea tareas Shell administradas. Cerrar una pestaña no detiene la tarea.|Affiche les tâches Shell gérées. Fermer un onglet ne les arrête pas.|Verwaltete Shell-Aufgaben anzeigen. Das Schließen eines Tabs stoppt die Aufgabe nicht.|Veja tarefas Shell gerenciadas. Fechar uma aba não encerra a tarefa.|Просмотр управляемых задач Shell. Закрытие вкладки не останавливает задачу.|管理対象の Shell タスクを表示します。タブを閉じても停止しません。|관리되는 Shell 작업을 표시합니다. 탭을 닫아도 작업은 중지되지 않습니다.
正在读取后台任务…|Loading background jobs…|Cargando tareas…|Chargement des tâches…|Hintergrundaufgaben werden geladen…|Carregando tarefas…|Загрузка фоновых задач…|タスクを読み込み中…|백그라운드 작업 로딩 중…
暂无后台任务|No background jobs|No hay tareas en segundo plano|Aucune tâche en arrière-plan|Keine Hintergrundaufgaben|Nenhuma tarefa em segundo plano|Нет фоновых задач|バックグラウンドタスクはありません|백그라운드 작업 없음
未绑定对话|No conversation linked|Sin conversación vinculada|Aucune conversation liée|Kein Gespräch verknüpft|Sem conversa vinculada|Диалог не привязан|会話の関連付けなし|연결된 대화 없음
退出码|Exit code|Código de salida|Code de sortie|Exit-Code|Código de saída|Код завершения|終了コード|종료 코드
输出流|Output stream|Flujo de salida|Flux de sortie|Ausgabestrom|Fluxo de saída|Поток вывода|出力ストリーム|출력 스트림
输出按流分组显示|Output is grouped by stream|Salida agrupada por flujo|Sortie regroupée par flux|Ausgabe nach Stream gruppiert|Saída agrupada por fluxo|Вывод сгруппирован по потокам|出力はストリーム別に表示|스트림별 출력 표시
仅显示最近 64 KiB 输出|Showing the latest 64 KiB|Se muestran los últimos 64 KiB|Affichage des derniers 64 Kio|Die letzten 64 KiB werden angezeigt|Exibindo os últimos 64 KiB|Показаны последние 64 КиБ|最新の 64 KiB を表示|최근 64 KiB 표시
搜索输出|Search output|Buscar en la salida|Rechercher dans la sortie|Ausgabe durchsuchen|Pesquisar saída|Поиск в выводе|出力を検索|출력 검색
复制输出|Copy output|Copiar salida|Copier la sortie|Ausgabe kopieren|Copiar saída|Копировать вывод|出力をコピー|출력 복사
刷新输出|Refresh output|Actualizar salida|Actualiser la sortie|Ausgabe aktualisieren|Atualizar saída|Обновить вывод|出力を更新|출력 새로고침
没有匹配的输出|No matching output|Sin resultados|Aucune sortie correspondante|Keine passende Ausgabe|Nenhuma saída correspondente|Совпадений нет|一致する出力はありません|일치하는 출력 없음
暂无输出|No output yet|Aún no hay salida|Aucune sortie pour le moment|Noch keine Ausgabe|Ainda sem saída|Вывода пока нет|出力はまだありません|아직 출력 없음
暂停跟随|Pause following|Pausar seguimiento|Suspendre le suivi|Verfolgen pausieren|Pausar acompanhamento|Приостановить слежение|追従を一時停止|자동 스크롤 일시 중지
当前实例启动终端的只读输出，与 Shell 后台任务分别展示。|Read-only output from this instance console, separate from Shell jobs.|Salida de solo lectura de esta instancia, separada de las tareas Shell.|Sortie en lecture seule de cette instance, distincte des tâches Shell.|Schreibgeschützte Instanzausgabe, getrennt von Shell-Aufgaben.|Saída somente leitura desta instância, separada das tarefas Shell.|Вывод консоли экземпляра только для чтения, отдельно от задач Shell.|このインスタンスの読み取り専用コンソール出力です。Shell タスクとは別に表示します。|이 인스턴스 콘솔의 읽기 전용 출력이며 Shell 작업과 별도로 표시됩니다.
状态未知|Unknown status|Estado desconocido|État inconnu|Unbekannter Status|Status desconhecido|Состояние неизвестно|状態不明|알 수 없는 상태
正在结束|Finishing|Finalizando|Finalisation|Wird beendet|Finalizando|Завершение|終了処理中|종료 중
最多同时打开 16 个标签，请先关闭部分标签。|Up to 16 tabs can be open. Close a tab first.|Puede abrir hasta 16 pestañas. Cierre una primero.|Vous pouvez ouvrir 16 onglets. Fermez-en un d’abord.|Bis zu 16 Tabs können geöffnet sein. Bitte zuerst einen schließen.|Até 16 abas podem ficar abertas. Feche uma primeiro.|Можно открыть до 16 вкладок. Сначала закройте одну.|最大 16 タブです。先にいずれかを閉じてください。|최대 16개 탭을 열 수 있습니다. 먼저 탭을 닫아 주세요.
智能体任务|Agent tasks|Tareas de agentes|Tâches des agents|Agentenaufgaben|Tarefas dos agentes|Задачи агентов|エージェントタスク|에이전트 작업
主任务|Main task|Tarea principal|Tâche principale|Hauptaufgabe|Tarefa principal|Основная задача|メインタスク|주 작업
任务总览|Task overview|Resumen de tareas|Vue des tâches|Aufgabenübersicht|Visão das tarefas|Обзор задач|タスク一覧|작업 개요
查看执行|View execution|Ver ejecución|Voir l’exécution|Ausführung ansehen|Ver execução|Просмотр выполнения|実行を表示|실행 보기
当前对话|Current conversation|Conversación actual|Conversation actuelle|Aktuelles Gespräch|Conversa atual|Текущий диалог|現在の会話|현재 대화
暂无智能体任务|No agent tasks yet|Aún no hay tareas de agentes|Aucune tâche d’agent|Noch keine Agentenaufgaben|Nenhuma tarefa de agente|Задач агентов пока нет|エージェントタスクはありません|에이전트 작업 없음
正在读取任务…|Loading tasks…|Cargando tareas…|Chargement des tâches…|Aufgaben werden geladen…|Carregando tarefas…|Загрузка задач…|タスクを読み込み中…|작업 불러오는 중…
排队中|Queued|En cola|En attente|Eingereiht|Na fila|В очереди|待機中|대기 중
执行中|Running|En ejecución|En cours|Wird ausgeführt|Em execução|Выполняется|実行中|실행 중
等待子任务|Waiting for child tasks|Esperando subtareas|En attente des sous-tâches|Warten auf Unteraufgaben|Aguardando subtarefas|Ожидание подзадач|子タスクの完了待ち|하위 작업 대기 중
汇总结果|Merging results|Integrando resultados|Synthèse des résultats|Ergebnisse zusammenführen|Consolidando resultados|Объединение результатов|結果を集約中|결과 종합 중
取消收尾中|Cancelling and finishing|Cancelando y finalizando|Annulation et clôture|Abbruch wird abgeschlossen|Cancelando e finalizando|Завершение отмены|キャンセル処理中|취소 마무리 중
执行超时|Execution timed out|Tiempo de ejecución agotado|Délai d’exécution dépassé|Zeitlimit überschritten|Tempo de execução esgotado|Время выполнения истекло|実行タイムアウト|실행 시간 초과
超时但仍在执行|Timed out, still running|Tiempo agotado, aún en ejecución|Délai dépassé, toujours en cours|Zeitlimit überschritten, läuft weiter|Tempo esgotado, ainda em execução|Время истекло, выполнение продолжается|タイムアウト後も実行中|시간 초과 후에도 실행 중
请求模型|Requesting model|Solicitando al modelo|Requête au modèle|Modell anfragen|Solicitando ao modelo|Запрос к модели|モデルにリクエスト中|모델 요청 중
执行工具|Running tool|Ejecutando herramienta|Exécution de l’outil|Werkzeug ausführen|Executando ferramenta|Выполнение инструмента|ツール実行中|도구 실행 중
工具已返回|Tool returned|Herramienta finalizada|Outil terminé|Werkzeug beendet|Ferramenta retornou|Инструмент завершён|ツール実行終了|도구 반환 완료
准备执行|Preparing execution|Preparando ejecución|Préparation de l’exécution|Ausführung vorbereiten|Preparando execução|Подготовка выполнения|実行準備中|실행 준비 중
校验结果|Validating result|Validando resultado|Validation du résultat|Ergebnis prüfen|Validando resultado|Проверка результата|結果を検証中|결과 검증 중
分派子任务|Dispatching child tasks|Asignando subtareas|Attribution des sous-tâches|Unteraufgaben zuweisen|Distribuindo subtarefas|Назначение подзадач|子タスクを割り当て中|하위 작업 배정 중
仅显示最近子任务|Showing recent child tasks|Se muestran las subtareas recientes|Sous-tâches récentes affichées|Nur letzte Unteraufgaben|Exibindo subtarefas recentes|Показаны последние подзадачи|最近の子タスクを表示|최근 하위 작업만 표시
必要依赖|Required dependency|Dependencia necesaria|Dépendance requise|Erforderliche Abhängigkeit|Dependência necessária|Обязательная зависимость|必須の依存タスク|필수 의존 작업
主智能体已处理结果|Main agent processed the result|El agente principal procesó el resultado|Résultat traité par l’agent principal|Ergebnis vom Hauptagenten verarbeitet|Resultado processado pelo agente principal|Основной агент обработал результат|メインエージェントが結果を処理済み|주 에이전트가 결과 처리 완료
等待主智能体处理结果|Waiting for main agent to process result|Esperando al agente principal|En attente du traitement par l’agent principal|Warten auf Verarbeitung durch Hauptagent|Aguardando processamento pelo agente principal|Ожидание обработки основным агентом|メインエージェントの結果処理待ち|주 에이전트의 결과 처리 대기 중
开始|Started|Inicio|Début|Beginn|Início|Начало|開始|시작
结束|Finished|Fin|Fin|Ende|Fim|Завершение|終了|종료
正在重试|Retrying|Reintentando|Nouvelle tentative|Erneuter Versuch|Tentando novamente|Повторная попытка|再試行中|재시도 중
执行时间线|Execution timeline|Cronología de ejecución|Chronologie d’exécution|Ausführungsverlauf|Linha do tempo de execução|Хронология выполнения|実行タイムライン|실행 타임라인
最终结果|Final result|Resultado final|Résultat final|Endergebnis|Resultado final|Итоговый результат|最終結果|최종 결과
文件产物|Output files|Archivos generados|Fichiers produits|Ausgabedateien|Arquivos gerados|Выходные файлы|生成ファイル|생성 파일
分派失败|Dispatch failed|Error al asignar|Échec de l’attribution|Zuweisung fehlgeschlagen|Falha na distribuição|Ошибка назначения|割り当て失敗|배정 실패
正在分派|Dispatching|Asignando|Attribution en cours|Wird zugewiesen|Distribuindo|Назначение|割り当て中|배정 중
已提交，等待执行状态|Submitted, awaiting execution status|Enviada, esperando estado|Soumis, en attente de l’état|Übermittelt, warte auf Ausführungsstatus|Enviada, aguardando status|Отправлено, ожидание состояния|送信済み、実行状態を確認中|제출됨, 실행 상태 대기 중
后台提交不代表完成；必要子任务退出并由主智能体处理结果后，主任务才会结束。关闭标签不会停止任务。|Background submission is not completion. The main task ends after required children exit and their results are processed. Closing a tab does not stop tasks.|Enviar en segundo plano no significa completar. La tarea principal termina cuando las subtareas necesarias finalizan y sus resultados se procesan. Cerrar una pestaña no detiene las tareas.|Un envoi en arrière-plan ne vaut pas achèvement. La tâche principale se termine après la fin des sous-tâches requises et le traitement des résultats. Fermer un onglet n’arrête pas les tâches.|Hintergrundübermittlung bedeutet keinen Abschluss. Die Hauptaufgabe endet, wenn erforderliche Unteraufgaben beendet und ihre Ergebnisse verarbeitet sind. Schließen eines Tabs stoppt keine Aufgaben.|Enviar em segundo plano não significa concluir. A tarefa principal termina após as subtarefas necessárias encerrarem e seus resultados serem processados. Fechar uma aba não interrompe tarefas.|Фоновая отправка не означает завершения. Основная задача завершится после выхода обязательных подзадач и обработки их результатов. Закрытие вкладки не останавливает задачи.|バックグラウンド送信は完了ではありません。必須の子タスクが終了し、結果の処理が済むとメインタスクが完了します。タブを閉じてもタスクは停止しません。|백그라운드 제출은 완료를 의미하지 않습니다. 필수 하위 작업이 종료되고 결과가 처리되어야 주 작업이 끝납니다. 탭을 닫아도 작업은 중지되지 않습니다.
仅展示公开阶段及工具执行摘要，不展示私有推理、系统提示词或凭据。最多保留最近 160 条事件。|Shows public stages and tool summaries only, without private reasoning, system prompts or credentials. Keeps up to 160 recent events.|Solo muestra fases públicas y resúmenes de herramientas, sin razonamiento privado, instrucciones del sistema ni credenciales. Conserva hasta 160 eventos recientes.|Affiche uniquement les étapes publiques et résumés des outils, sans raisonnement privé, instructions système ni identifiants. Conserve les 160 derniers événements.|Zeigt öffentliche Phasen und Werkzeugzusammenfassungen, ohne private Gedankengänge, Systemanweisungen oder Zugangsdaten. Speichert bis zu 160 letzte Ereignisse.|Exibe apenas etapas públicas e resumos de ferramentas, sem raciocínio privado, instruções do sistema ou credenciais. Mantém até 160 eventos recentes.|Показаны только открытые этапы и сводки инструментов, без скрытых рассуждений, системных инструкций и учётных данных. Хранятся последние 160 событий.|公開ステージとツール実行の概要のみ表示し、非公開の推論、システム指示、認証情報は表示しません。直近 160 件まで保持します。|공개 단계와 도구 실행 요약만 표시하며 비공개 추론, 시스템 지침, 자격 증명은 표시하지 않습니다. 최근 이벤트를 최대 160개 보관합니다.
拥塞与并发|Congestion and concurrency|Congestión y concurrencia|Congestion et concurrence|Auslastung und Parallelität|Congestionamento e concorrência|Нагрузка и параллельность|混雑と同時実行|혼잡 및 동시 실행
Provider 请求|Provider requests|Solicitudes al proveedor|Requêtes fournisseur|Provider-Anfragen|Solicitações ao provedor|Запросы к провайдеру|プロバイダーへのリクエスト|공급자 요청
Web 聊天槽位|Web chat slots|Turnos de chat web|Créneaux de chat Web|Web-Chat-Plätze|Vagas de chat web|Слоты веб-чата|Web チャット枠|웹 채팅 슬롯
消息工作线程|Message workers|Procesos de mensajes|Workers de messages|Nachrichten-Worker|Workers de mensagens|Обработчики сообщений|メッセージワーカー|메시지 워커
占用|In use|En uso|Utilisé|Belegt|Em uso|Занято|使用中|사용 중
可用|Available|Disponible|Disponible|Verfügbar|Disponível|Доступно|利用可能|사용 가능
排队|Queued|En cola|En attente|In Warteschlange|Na fila|В очереди|待機中|대기 중
刷新并发状态|Refresh concurrency status|Actualizar estado de concurrencia|Actualiser la concurrence|Parallelitätsstatus aktualisieren|Atualizar concorrência|Обновить состояние параллельности|同時実行状況を更新|동시 실행 상태 새로 고침
Provider 请求池仅统计模型请求，工具执行期间不占用。|The Provider pool counts model requests only; tool execution does not occupy a slot.|El grupo Provider solo cuenta solicitudes al modelo; las herramientas no ocupan turnos.|Le pool Provider compte uniquement les requêtes au modèle ; les outils n’occupent pas de créneau.|Der Provider-Pool zählt nur Modellanfragen; Werkzeugausführung belegt keinen Platz.|O pool Provider conta apenas solicitações ao modelo; ferramentas não ocupam vagas.|Пул Provider учитывает только запросы к модели; выполнение инструментов не занимает слот.|Provider プールはモデルへのリクエストのみを計数し、ツール実行中は枠を使用しません。|Provider 풀은 모델 요청만 집계하며 도구 실행 중에는 슬롯을 차지하지 않습니다.
刷新失败，当前显示上次成功读取的数据。|Refresh failed; showing the last successfully loaded data.|Error al actualizar; se muestran los últimos datos cargados.|Échec de l’actualisation ; affichage des dernières données chargées.|Aktualisierung fehlgeschlagen; zuletzt geladene Daten werden angezeigt.|Falha ao atualizar; mostrando os últimos dados carregados.|Ошибка обновления; показаны последние успешно полученные данные.|更新に失敗しました。前回正常に読み込んだデータを表示しています。|새로 고침에 실패하여 마지막으로 불러온 데이터를 표시합니다.
Token 总命中率|Total token cache hit rate|Tasa total de aciertos de caché de tokens|Taux global de succès du cache de tokens|Gesamte Token-Cache-Trefferrate|Taxa total de acerto do cache de tokens|Общий процент попаданий в кэш токенов|Token キャッシュ総ヒット率|토큰 전체 캐시 적중률
当前对话空间累计|Cumulative for this conversation|Acumulado en esta conversación|Cumul pour cette conversation|Gesamt für diese Unterhaltung|Acumulado nesta conversa|Накоплено в этом диалоге|この会話の累計|현재 대화 누적
`
