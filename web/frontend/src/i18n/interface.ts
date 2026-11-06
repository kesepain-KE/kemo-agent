/** First-party interface text. Columns follow locales.ts. */
export const interfaceMessages = `
kemo-agent 已就绪。描述目标即可开始，需要计划的任务会先生成草案再等确认。|kemo-agent is ready. Describe your goal to begin. Tasks that need a plan will start with a draft for your approval.|kemo-agent está listo. Describe tu objetivo. Las tareas que necesitan un plan comenzarán con un borrador para tu aprobación.|kemo-agent est prêt. Décrivez votre objectif. Les tâches nécessitant un plan commenceront par un brouillon à approuver.|kemo-agent ist bereit. Beschreibe dein Ziel. Aufgaben, die einen Plan benötigen, beginnen mit einem Entwurf zur Freigabe.|kemo-agent está pronto. Descreva seu objetivo. Tarefas que precisam de um plano começarão com um rascunho para sua aprovação.|kemo-agent готов. Опишите цель. Для задач, которым нужен план, сначала будет создан черновик для согласования.|kemo-agent の準備ができました。目標を入力してください。計画が必要なタスクでは、承認用の下書きを先に作成します。|kemo-agent가 준비되었습니다. 목표를 입력하세요. 계획이 필요한 작업은 먼저 승인할 초안을 만듭니다.
近期活动|Recent activity|Actividad reciente|Activité récente|Letzte Aktivitäten|Atividade recente|Недавняя активность|最近のアクティビティ|최근 활동
系统状态|System status|Estado del sistema|État du système|Systemstatus|Status do sistema|Состояние системы|システム状態|시스템 상태
暂无待执行的定时任务|No scheduled tasks pending|No hay tareas programadas pendientes|Aucune tâche planifiée en attente|Keine geplanten Aufgaben ausstehend|Nenhuma tarefa agendada pendente|Нет ожидающих запланированных задач|実行待ちの定期タスクはありません|대기 중인 예약 작업이 없습니다
Token 统计|Token statistics|Estadísticas de tokens|Statistiques des tokens|Token-Statistik|Estatísticas de tokens|Статистика токенов|トークン統計|토큰 통계
今日累计|Today's total|Total de hoy|Total du jour|Heute gesamt|Total de hoje|Всего за сегодня|本日の合計|오늘 누적
Token 命中率|Token cache hit rate|Tasa de aciertos de caché|Taux de succès du cache|Token-Cache-Trefferquote|Taxa de acertos do cache|Доля попаданий в кеш токенов|トークンキャッシュヒット率|토큰 캐시 적중률
缓存命中|Cache hits|Aciertos de caché|Succès du cache|Cache-Treffer|Acertos do cache|Попадания в кеш|キャッシュヒット|캐시 적중
今日输出|Today's output|Salida de hoy|Sortie du jour|Heutige Ausgabe|Saída de hoje|Вывод за сегодня|本日の出力|오늘 출력
收起步骤|Collapse steps|Contraer pasos|Réduire les étapes|Schritte einklappen|Recolher etapas|Свернуть шаги|ステップを折りたたむ|단계 접기
展开步骤|Expand steps|Expandir pasos|Développer les étapes|Schritte ausklappen|Expandir etapas|Развернуть шаги|ステップを展開|단계 펼치기
正在加载历史…|Loading history…|Cargando historial…|Chargement de l’historique…|Verlauf wird geladen…|Carregando histórico…|Загрузка истории…|履歴を読み込み中…|기록을 불러오는 중…
历史读取失败，请刷新后重试；不会把读取错误当作空会话。|History could not be loaded. Refresh and retry; a loading error does not mean the conversation is empty.|No se pudo cargar el historial. Actualiza y reintenta; un error no significa que la conversación esté vacía.|Impossible de charger l’historique. Actualisez et réessayez ; une erreur ne signifie pas que la conversation est vide.|Der Verlauf konnte nicht geladen werden. Bitte aktualisieren; ein Ladefehler bedeutet keinen leeren Chat.|Não foi possível carregar o histórico. Atualize e tente novamente; um erro não significa que a conversa está vazia.|Не удалось загрузить историю. Обновите страницу; ошибка загрузки не означает, что диалог пуст.|履歴を読み込めませんでした。更新して再試行してください。読み込みエラーを空の会話として扱うことはありません。|기록을 불러오지 못했습니다. 새로 고침 후 다시 시도하세요. 오류를 빈 대화로 처리하지 않습니다.
正在加载更早对话…|Loading earlier conversations…|Cargando conversaciones anteriores…|Chargement des conversations précédentes…|Ältere Chats werden geladen…|Carregando conversas anteriores…|Загрузка более ранних диалогов…|過去の会話を読み込み中…|이전 대화를 불러오는 중…
已到达对话开头|Beginning of conversation|Inicio de la conversación|Début de la conversation|Beginn des Chats|Início da conversa|Начало диалога|会話の先頭です|대화의 시작입니다
编辑后重发|Edit and resend|Editar y reenviar|Modifier et renvoyer|Bearbeiten und erneut senden|Editar e reenviar|Изменить и отправить снова|編集して再送|편집 후 다시 보내기
已用于重发|Used for resend|Usado para reenvío|Utilisé pour le renvoi|Für erneutes Senden verwendet|Usado para reenvio|Использовано для повторной отправки|再送に使用済み|다시 보내기에 사용됨
正在撤销…|Undoing…|Deshaciendo…|Annulation…|Wird rückgängig gemacht…|Desfazendo…|Отмена…|取り消し中…|실행 취소 중…
编辑重发|Edit and resend|Editar y reenviar|Modifier et renvoyer|Bearbeiten und erneut senden|Editar e reenviar|Изменить и отправить снова|編集して再送|편집 후 다시 보내기
复制消息|Copy message|Copiar mensaje|Copier le message|Nachricht kopieren|Copiar mensagem|Копировать сообщение|メッセージをコピー|메시지 복사
运行出现问题，正在自动重试（本次错误第 {attempt}/{max} 次）|A run error occurred. Retrying automatically ({attempt}/{max} for this error).|Se produjo un error. Reintentando automáticamente ({attempt}/{max} para este error).|Erreur d’exécution. Nouvelle tentative automatique ({attempt}/{max} pour cette erreur).|Ausführungsfehler. Automatischer Neuversuch ({attempt}/{max} für diesen Fehler).|Ocorreu um erro. Tentando automaticamente ({attempt}/{max} para este erro).|Ошибка выполнения. Автоматический повтор ({attempt}/{max} для этой ошибки).|実行エラーが発生しました。自動再試行中です（このエラーの {attempt}/{max} 回目）。|실행 오류가 발생했습니다. 자동 재시도 중입니다(이 오류의 {attempt}/{max}회).
关闭运行错误提示|Dismiss run error|Cerrar error de ejecución|Fermer l’erreur d’exécution|Ausführungsfehler schließen|Fechar erro de execução|Закрыть сообщение об ошибке|実行エラーを閉じる|실행 오류 알림 닫기
继续跟随最新回复|Follow latest reply|Seguir la última respuesta|Suivre la dernière réponse|Neuester Antwort folgen|Acompanhar resposta mais recente|Следить за последним ответом|最新の返信に追従|최신 답변 따라가기
Enter 加入消息跟进；Ctrl+Enter 直接本轮引导…|Enter to queue a follow-up; Ctrl+Enter to guide this turn…|Enter para poner en cola; Ctrl+Enter para orientar este turno…|Entrée pour mettre en attente ; Ctrl+Entrée pour guider ce tour…|Enter für eine Folgenachricht; Strg+Enter zum Steuern dieser Runde…|Enter para enfileirar; Ctrl+Enter para orientar este turno…|Enter — сообщение в очередь; Ctrl+Enter — указание для текущего хода…|Enter で次のメッセージを追加、Ctrl+Enter で現在のターンを誘導…|Enter로 후속 메시지 추가, Ctrl+Enter로 현재 턴 안내…
给 kemo-agent 发送消息…|Message kemo-agent…|Envía un mensaje a kemo-agent…|Écrire à kemo-agent…|Nachricht an kemo-agent…|Envie uma mensagem ao kemo-agent…|Напишите kemo-agent…|kemo-agent にメッセージを送信…|kemo-agent에게 메시지 보내기…
关闭上传提示|Dismiss upload notice|Cerrar aviso de carga|Fermer l’avis d’importation|Upload-Hinweis schließen|Fechar aviso de envio|Закрыть уведомление о загрузке|アップロード通知を閉じる|업로드 알림 닫기
下一轮跟进|Follow up next turn|Continuar en el próximo turno|Suivi au prochain tour|Nächste Runde ergänzen|Continuar no próximo turno|Сообщение на следующий ход|次のターンで続行|다음 턴 후속 메시지
换行|New line|Nueva línea|Nouvelle ligne|Zeilenumbruch|Nova linha|Новая строка|改行|줄 바꿈
引导当前轮|Guide current turn|Orientar turno actual|Guider le tour actuel|Aktuelle Runde steuern|Orientar turno atual|Указание для текущего хода|現在のターンを誘導|현재 턴 안내
正在加载模块…|Loading module…|Cargando módulo…|Chargement du module…|Modul wird geladen…|Carregando módulo…|Загрузка модуля…|モジュールを読み込み中…|모듈을 불러오는 중…
关闭通知|Dismiss notification|Cerrar notificación|Fermer la notification|Benachrichtigung schließen|Fechar notificação|Закрыть уведомление|通知を閉じる|알림 닫기
操作窗口|Action dialog|Ventana de acción|Fenêtre d’action|Aktionsdialog|Janela de ação|Окно действия|操作ダイアログ|작업 창
确认操作|Confirm action|Confirmar acción|Confirmer l’action|Aktion bestätigen|Confirmar ação|Подтвердить действие|操作を確認|작업 확인
切换|Switch|Cambiar|Changer|Wechseln|Alternar|Переключить|切り替え|전환
执行失败|Execution failed|Error de ejecución|Échec de l’exécution|Ausführung fehlgeschlagen|Falha na execução|Выполнение не удалось|実行に失敗|실행 실패
查看全部|View all|Ver todo|Tout afficher|Alle anzeigen|Ver tudo|Показать все|すべて表示|전체 보기
最近状态|Recent status|Estado reciente|État récent|Aktueller Status|Status recente|Последнее состояние|最近の状態|최근 상태
当前没有已配置的用户定时任务|No user schedules configured|No hay tareas de usuario configuradas|Aucune tâche utilisateur configurée|Keine Benutzeraufgaben geplant|Nenhuma tarefa de usuário configurada|Нет настроенных задач пользователя|ユーザーの定期タスクは未設定です|설정된 사용자 예약 작업이 없습니다
下次运行：|Next run:|Próxima ejecución:|Prochaine exécution :|Nächste Ausführung:|Próxima execução:|Следующий запуск:|次回実行：|다음 실행:
频率未声明|Interval not specified|Intervalo sin especificar|Intervalle non précisé|Intervall nicht angegeben|Intervalo não informado|Интервал не указан|間隔は未指定です|주기 미지정
最新值：|Latest value:|Último valor:|Dernière valeur :|Letzter Wert:|Último valor:|Последнее значение:|最新値：|최신 값:
暂无数据|No data|Sin datos|Aucune donnée|Keine Daten|Sem dados|Нет данных|データなし|데이터 없음
更新时间：|Updated:|Actualizado:|Mise à jour :|Aktualisiert:|Atualizado:|Обновлено:|更新日時：|업데이트:
未注入|Not injected|No inyectado|Non injecté|Nicht eingefügt|Não injetado|Не добавлено|未注入|주입되지 않음
第 {attempt} 次尝试未完成 · 已保留快照|Attempt {attempt} did not finish · Snapshot retained|Intento {attempt} sin completar · Instantánea conservada|Tentative {attempt} inachevée · Instantané conservé|Versuch {attempt} nicht abgeschlossen · Snapshot gespeichert|Tentativa {attempt} incompleta · Snapshot mantido|Попытка {attempt} не завершена · Снимок сохранён|試行 {attempt} は未完了 · スナップショットを保持|{attempt}번째 시도 미완료 · 스냅샷 보존
重新发送|Resend|Reenviar|Renvoyer|Erneut senden|Reenviar|Отправить повторно|再送|다시 보내기
引导中|Guiding|Orientando|Guidage en cours|Wird gesteuert|Orientando|Передача указания|誘導中|안내 중
引导成功|Guidance applied|Orientación aplicada|Guidage appliqué|Steuerung angewendet|Orientação aplicada|Указание применено|誘導を適用済み|안내 적용됨
引导失败|Guidance failed|Error de orientación|Échec du guidage|Steuerung fehlgeschlagen|Falha na orientação|Не удалось передать указание|誘導に失敗|안내 실패
正在引导|Sending guidance|Enviando orientación|Envoi du guidage|Steuerung wird gesendet|Enviando orientação|Отправка указания|誘導を送信中|안내 전송 중
自动发送失败|Automatic send failed|Error de envío automático|Échec de l’envoi automatique|Automatisches Senden fehlgeschlagen|Falha no envio automático|Автоматическая отправка не удалась|自動送信に失敗|자동 전송 실패
已排队到下一轮|Queued for next turn|En cola para el próximo turno|En attente du prochain tour|Für nächste Runde eingereiht|Na fila para o próximo turno|В очереди на следующий ход|次のターンに追加済み|다음 턴에 대기 중
本轮未生效|Not applied this turn|No aplicado en este turno|Non appliqué à ce tour|In dieser Runde nicht angewendet|Não aplicado neste turno|Не применено в этом ходе|このターンでは未適用|현재 턴에 적용되지 않음
等待智能体到达下一个安全边界|Waiting for the agent's next safe boundary|Esperando el siguiente punto seguro del agente|En attente du prochain point sûr de l’agent|Warten auf den nächsten sicheren Punkt des Agenten|Aguardando o próximo ponto seguro do agente|Ожидание следующей безопасной границы агента|エージェントの次の安全な区切りを待機中|에이전트의 다음 안전 지점 대기 중
本轮结束后将自动作为新的用户消息发送|Will be sent as a new user message after this turn|Se enviará como nuevo mensaje al finalizar el turno|Sera envoyé comme nouveau message après ce tour|Wird nach dieser Runde als neue Nachricht gesendet|Será enviada como nova mensagem após este turno|Будет отправлено новым сообщением после этого хода|このターンの終了後、新しいユーザーメッセージとして自動送信します|현재 턴이 끝나면 새 사용자 메시지로 자동 전송됩니다
消息仍已保留，可以重新发送|The message is retained and can be resent|El mensaje se conserva y puede reenviarse|Le message est conservé et peut être renvoyé|Die Nachricht bleibt erhalten und kann erneut gesendet werden|A mensagem foi mantida e pode ser reenviada|Сообщение сохранено, его можно отправить снова|メッセージは保持されており、再送できます|메시지가 보존되어 다시 보낼 수 있습니다
智能体已读取该引导并继续运行|The agent has read the guidance and resumed|El agente leyó la orientación y continuó|L’agent a lu le guidage et a repris|Der Agent hat die Steuerung gelesen und fortgesetzt|O agente leu a orientação e continuou|Агент прочитал указание и продолжил работу|エージェントは誘導を読み取り、実行を続行しました|에이전트가 안내를 읽고 실행을 계속했습니다
本轮运行已采用此引导|Guidance used in this turn|Orientación usada en este turno|Guidage utilisé pour ce tour|Steuerung in dieser Runde verwendet|Orientação usada neste turno|Указание использовано в этом ходе|このターンに誘導を適用しました|현재 턴에 안내가 사용되었습니다
本轮结束前未进入下一次模型请求|No subsequent model request before this turn ended|No hubo otra solicitud al modelo antes de finalizar el turno|Aucune nouvelle requête au modèle avant la fin du tour|Vor Rundenende erfolgte keine weitere Modellanfrage|Não houve nova solicitação ao modelo antes do fim do turno|До завершения хода не было следующего запроса к модели|ターン終了までに次のモデルリクエストが発生しませんでした|턴이 끝나기 전 다음 모델 요청이 없었습니다
引导未能提交到当前运行|Could not submit guidance to this run|No se pudo enviar orientación a esta ejecución|Impossible d’envoyer le guidage à cette exécution|Steuerung konnte nicht übermittelt werden|Não foi possível enviar orientação a esta execução|Не удалось передать указание текущему запуску|現在の実行に誘導を送信できませんでした|현재 실행에 안내를 제출하지 못했습니다
对话压缩失败|Conversation compression failed|Error al comprimir la conversación|Échec de la compression de la conversation|Chat-Komprimierung fehlgeschlagen|Falha ao compactar conversa|Не удалось сжать диалог|会話の圧縮に失敗|대화 압축 실패
对话上下文已压缩|Conversation context compressed|Contexto de conversación comprimido|Contexte de conversation compressé|Chatkontext komprimiert|Contexto da conversa compactado|Контекст диалога сжат|会話コンテキストを圧縮済み|대화 컨텍스트 압축됨
正在压缩对话上下文|Compressing conversation context|Comprimiendo contexto de conversación|Compression du contexte de conversation|Chatkontext wird komprimiert|Compactando contexto da conversa|Сжатие контекста диалога|会話コンテキストを圧縮中|대화 컨텍스트 압축 중
正在生成历史摘要|Generating history summary|Generando resumen del historial|Génération du résumé de l’historique|Verlaufszusammenfassung wird erstellt|Gerando resumo do histórico|Создание сводки истории|履歴の要約を生成中|기록 요약 생성 중
已整理 {count} 张历史卡片，历史 Token {before} → {after}|Processed {count} history cards; history tokens {before} → {after}|Se procesaron {count} tarjetas; tokens del historial {before} → {after}|{count} fiches traitées ; tokens de l’historique {before} → {after}|{count} Verlaufskarten verarbeitet; Verlaufstokens {before} → {after}|{count} cartões processados; tokens do histórico {before} → {after}|Обработано карточек: {count}; токены истории {before} → {after}|履歴カード {count} 件を整理、履歴トークン {before} → {after}|기록 카드 {count}개 정리, 기록 토큰 {before} → {after}
已创建任务计划|Task plan created|Plan creado|Plan créé|Aufgabenplan erstellt|Plano criado|План задач создан|タスク計画を作成済み|작업 계획 생성됨
查看当前计划|View current plan|Ver plan actual|Voir le plan actuel|Aktuellen Plan anzeigen|Ver plano atual|Открыть текущий план|現在の計画を表示|현재 계획 보기
每天 {time}|Daily at {time}|Cada día a las {time}|Chaque jour à {time}|Täglich um {time}|Diariamente às {time}|Ежедневно в {time}|毎日 {time}|매일 {time}
单次 · {time}|Once · {time}|Una vez · {time}|Une fois · {time}|Einmal · {time}|Uma vez · {time}|Однократно · {time}|1回 · {time}|한 번 · {time}
每 {count} 小时|Every {count} hours|Cada {count} horas|Toutes les {count} heures|Alle {count} Stunden|A cada {count} horas|Каждые {count} ч|{count} 時間ごと|{count}시간마다
每 {count} 分钟|Every {count} minutes|Cada {count} minutos|Toutes les {count} minutes|Alle {count} Minuten|A cada {count} minutos|Каждые {count} мин|{count} 分ごと|{count}분마다
每 {count} 秒|Every {count} seconds|Cada {count} segundos|Toutes les {count} secondes|Alle {count} Sekunden|A cada {count} segundos|Каждые {count} с|{count} 秒ごと|{count}초마다
未配置调度|No schedule configured|Sin programación|Aucune planification|Kein Zeitplan konfiguriert|Sem agendamento|Расписание не задано|スケジュール未設定|일정 미설정
输入框切换模型|Composer model selector|Selector de modelo del editor|Sélecteur de modèle de saisie|Modellauswahl im Eingabefeld|Seletor de modelo da entrada|Выбор модели в поле ввода|入力欄のモデル切り替え|입력창 모델 선택
当前对话运行或操作中，结束后可切换模型|You can change the model when the current run or operation ends|Puedes cambiar el modelo al terminar la ejecución u operación|Vous pourrez changer de modèle après l’exécution ou l’opération|Modellwechsel nach Abschluss der aktuellen Ausführung möglich|Você poderá mudar o modelo após a execução ou operação|Сменить модель можно после завершения текущей операции|現在の実行または操作の終了後にモデルを切り替えられます|현재 실행 또는 작업이 끝나면 모델을 변경할 수 있습니다
切换模型|Change model|Cambiar modelo|Changer de modèle|Modell wechseln|Mudar modelo|Сменить модель|モデルを切り替え|모델 변경
请先配置 Provider|Configure a provider first|Configura primero un proveedor|Configurez d’abord un fournisseur|Zuerst Provider konfigurieren|Configure um provedor primeiro|Сначала настройте провайдер|先に Provider を設定してください|먼저 Provider를 설정하세요
尚未配置模型|No model configured|Sin modelo configurado|Aucun modèle configuré|Kein Modell konfiguriert|Nenhum modelo configurado|Модель не настроена|モデル未設定|모델 미설정
切换对话模型|Change conversation model|Cambiar modelo de conversación|Changer le modèle de conversation|Chatmodell wechseln|Mudar modelo da conversa|Сменить модель диалога|会話モデルを切り替え|대화 모델 변경
刷新模型列表|Refresh model list|Actualizar lista de modelos|Actualiser les modèles|Modellliste aktualisieren|Atualizar modelos|Обновить список моделей|モデル一覧を更新|모델 목록 새로 고침
搜索可用模型|Search available models|Buscar modelos disponibles|Rechercher les modèles disponibles|Verfügbare Modelle suchen|Pesquisar modelos disponíveis|Поиск доступных моделей|利用可能なモデルを検索|사용 가능한 모델 검색
搜索模型…|Search models…|Buscar modelos…|Rechercher un modèle…|Modelle suchen…|Pesquisar modelos…|Поиск моделей…|モデルを検索…|모델 검색…
正在获取模型列表…|Loading models…|Cargando modelos…|Chargement des modèles…|Modelle werden geladen…|Carregando modelos…|Загрузка моделей…|モデル一覧を取得中…|모델 목록 불러오는 중…
模型列表获取失败：|Failed to load models:|Error al cargar modelos:|Échec du chargement des modèles :|Modellliste konnte nicht geladen werden:|Falha ao carregar modelos:|Не удалось загрузить модели:|モデル一覧の取得に失敗：|모델 목록 불러오기 실패:
模型切换失败，已保留原模型，请重试。|Model change failed. The previous model is retained. Please retry.|No se pudo cambiar el modelo. Se conserva el anterior. Reintenta.|Le changement a échoué. Le modèle précédent est conservé. Réessayez.|Modellwechsel fehlgeschlagen. Das bisherige Modell bleibt erhalten. Bitte erneut versuchen.|Falha na mudança. O modelo anterior foi mantido. Tente novamente.|Не удалось сменить модель. Прежняя модель сохранена. Повторите попытку.|モデルの切り替えに失敗しました。元のモデルを保持しています。再試行してください。|모델 변경에 실패했습니다. 기존 모델이 유지됩니다. 다시 시도하세요.
正在保存模型…|Saving model…|Guardando modelo…|Enregistrement du modèle…|Modell wird gespeichert…|Salvando modelo…|Сохранение модели…|モデルを保存中…|모델 저장 중…
可用对话模型|Available chat models|Modelos de chat disponibles|Modèles de discussion disponibles|Verfügbare Chatmodelle|Modelos de conversa disponíveis|Доступные модели диалога|利用可能な会話モデル|사용 가능한 대화 모델
没有匹配的模型|No matching models|No hay modelos coincidentes|Aucun modèle correspondant|Keine passenden Modelle|Nenhum modelo correspondente|Нет подходящих моделей|一致するモデルはありません|일치하는 모델이 없습니다
当前接口未返回可用模型|The API returned no available models|La API no devolvió modelos disponibles|L’API n’a renvoyé aucun modèle disponible|Die API hat keine verfügbaren Modelle geliefert|A API não retornou modelos disponíveis|API не вернул доступных моделей|API が利用可能なモデルを返しませんでした|API가 사용 가능한 모델을 반환하지 않았습니다
请刷新重试或检查 Provider 配置。|Refresh and retry, or check provider settings.|Actualiza y reintenta, o revisa el proveedor.|Actualisez et réessayez, ou vérifiez le fournisseur.|Aktualisieren und erneut versuchen oder Provider prüfen.|Atualize e tente novamente, ou verifique o provedor.|Обновите и повторите попытку или проверьте настройки провайдера.|更新して再試行するか、Provider 設定を確認してください。|새로 고침 후 다시 시도하거나 Provider 설정을 확인하세요.
当前后端尚未加载模型列表接口，请重启智能体后重试。|The backend has not loaded the model-list endpoint. Restart the agent and retry.|El backend no ha cargado la lista de modelos. Reinicia el agente y reintenta.|Le serveur n’a pas chargé l’API des modèles. Redémarrez l’agent et réessayez.|Das Backend hat die Modellschnittstelle noch nicht geladen. Agent neu starten und erneut versuchen.|O servidor não carregou a API de modelos. Reinicie o agente e tente novamente.|Сервер ещё не загрузил API списка моделей. Перезапустите агента.|バックエンドにモデル一覧 API が未反映です。エージェントを再起動して再試行してください。|백엔드에 모델 목록 API가 로드되지 않았습니다. 에이전트를 재시작하고 다시 시도하세요.
KEMO · 内置模型目录|KEMO · Built-in models|KEMO · Modelos integrados|KEMO · Modèles intégrés|KEMO · Integrierte Modelle|KEMO · Modelos integrados|KEMO · Встроенные модели|KEMO · 内蔵モデル一覧|KEMO · 내장 모델 목록
Chat · 模型 API 列表|Chat · Model API list|Chat · Lista de modelos de la API|Chat · Modèles de l’API|Chat · API-Modellliste|Chat · Lista de modelos da API|Chat · Модели API|Chat · モデル API 一覧|Chat · 모델 API 목록
阅读中|Reading|Leyendo|Lecture|Wird gelesen|Lendo|Чтение|読み取り中|읽는 중
没有匹配的对话操作|No matching chat actions|No hay acciones coincidentes|Aucune action correspondante|Keine passenden Chataktionen|Nenhuma ação correspondente|Нет подходящих действий|一致する会話操作はありません|일치하는 대화 작업이 없습니다
开始语音识别|Start speech recognition|Iniciar reconocimiento de voz|Démarrer la reconnaissance vocale|Spracherkennung starten|Iniciar reconhecimento de voz|Начать распознавание речи|音声認識を開始|음성 인식 시작
Web 会话 · {id}|Web chat · {id}|Chat web · {id}|Discussion web · {id}|Webchat · {id}|Conversa web · {id}|Веб-диалог · {id}|Web 会話 · {id}|웹 대화 · {id}
{count} 次整合|{count} turns|{count} turnos|{count} tours|{count} Runden|{count} turnos|Ходов: {count}|{count} ターン|{count}턴
{count} 张卡片|{count} cards|{count} tarjetas|{count} fiches|{count} Karten|{count} cartões|Карточек: {count}|{count} カード|카드 {count}개
{count} 个|{count}|{count}|{count}|{count}|{count}|{count}|{count} 件|{count}개
{count} 项|{count} items|{count} elementos|{count} éléments|{count} Einträge|{count} itens|Элементов: {count}|{count} 件|{count}개 항목
{count} 字符|{count} characters|{count} caracteres|{count} caractères|{count} Zeichen|{count} caracteres|Символов: {count}|{count} 文字|{count}자
未配置|Not configured|Sin configurar|Non configuré|Nicht konfiguriert|Não configurado|Не настроено|未設定|설정되지 않음
未声明|Not declared|Sin declarar|Non déclaré|Nicht angegeben|Não declarado|Не указано|未指定|명시되지 않음
读取能力中…|Loading capabilities…|Cargando capacidades…|Chargement des capacités…|Fähigkeiten werden geladen…|Carregando capacidades…|Загрузка возможностей…|機能を読み込み中…|기능 불러오는 중…
推理不可用|Reasoning unavailable|Razonamiento no disponible|Raisonnement indisponible|Denken nicht verfügbar|Raciocínio indisponível|Рассуждение недоступно|推論は利用できません|추론 사용 불가
模型未声明推理档位|No reasoning levels declared|Sin niveles de razonamiento declarados|Aucun niveau de raisonnement déclaré|Keine Denkstufen angegeben|Sem níveis de raciocínio declarados|Уровни рассуждения не указаны|推論レベルは未指定です|추론 수준이 명시되지 않았습니다
模型能力读取失败|Failed to load model capabilities|Error al cargar capacidades|Échec du chargement des capacités|Modellfähigkeiten konnten nicht geladen werden|Falha ao carregar capacidades|Не удалось загрузить возможности модели|モデル機能の読み込みに失敗|모델 기능 불러오기 실패
思考强度保存失败，请重试。|Failed to save reasoning level. Please retry.|No se pudo guardar el nivel. Reintenta.|Impossible d’enregistrer le niveau. Réessayez.|Denkstufe konnte nicht gespeichert werden. Bitte erneut versuchen.|Falha ao salvar o nível. Tente novamente.|Не удалось сохранить уровень рассуждения. Повторите попытку.|推論レベルを保存できませんでした。再試行してください。|추론 수준을 저장하지 못했습니다. 다시 시도하세요.
不注入|Disabled|Desactivado|Désactivée|Deaktiviert|Desativada|Отключено|注入しない|주입 안 함
未进入系统提示词|Not included in system prompt|No incluido en el prompt del sistema|Non inclus dans le prompt système|Nicht im Systemprompt enthalten|Não incluído no prompt do sistema|Не включено в системный промпт|システムプロンプトに含まれません|시스템 프롬프트에 포함되지 않음
动态注入|Dynamic injection|Inyección dinámica|Injection dynamique|Dynamische Einbindung|Injeção dinâmica|Динамическое включение|動的注入|동적 주입
每次请求读取最新快照|Latest snapshot read on each request|Instantánea más reciente en cada solicitud|Dernier instantané lu à chaque requête|Aktueller Snapshot bei jeder Anfrage|Snapshot mais recente a cada solicitação|Последний снимок при каждом запросе|各リクエストで最新のスナップショットを読み込み|요청마다 최신 스냅샷을 읽음
已载入|Loaded|Cargado|Chargé|Geladen|Carregado|Загружено|読み込み済み|불러옴
已锁定|Locked|Bloqueado|Verrouillé|Gesperrt|Bloqueado|Заблокировано|ロック済み|잠김
重命名中…|Renaming…|Renombrando…|Renommage…|Wird umbenannt…|Renomeando…|Переименование…|名前を変更中…|이름 변경 중…
新名称|New name|Nuevo nombre|Nouveau nom|Neuer Name|Novo nome|Новое имя|新しい名前|새 이름
正在删除…|Deleting…|Eliminando…|Suppression…|Wird gelöscht…|Excluindo…|Удаление…|削除中…|삭제 중…
删除中…|Deleting…|Eliminando…|Suppression…|Wird gelöscht…|Excluindo…|Удаление…|削除中…|삭제 중…
页面渲染失败：{error}|Page could not be rendered: {error}|No se pudo mostrar la página: {error}|Impossible d’afficher la page : {error}|Seite konnte nicht angezeigt werden: {error}|Não foi possível exibir a página: {error}|Не удалось отобразить страницу: {error}|ページを表示できませんでした：{error}|페이지 표시 실패: {error}
按北京时间配置任务；执行内容应能在每次触发时独立理解。|Configure tasks in China Standard Time; each execution's content should be understandable on its own.|Configura las tareas según la hora de China; el contenido debe entenderse por sí solo en cada ejecución.|Configurez les tâches selon l’heure de Chine ; le contenu doit être compréhensible de façon autonome à chaque exécution.|Aufgaben nach chinesischer Standardzeit konfigurieren; der Inhalt muss bei jeder Ausführung eigenständig verständlich sein.|Configure tarefas pelo horário da China; o conteúdo deve ser compreensível por si só em cada execução.|Настраивайте задачи по китайскому времени; содержимое должно быть понятно при каждом отдельном запуске.|中国時間でタスクを設定してください。実行内容は毎回単独で理解できるようにします。|중국 표준시로 작업을 설정하세요. 실행 내용은 매번 독립적으로 이해할 수 있어야 합니다.
任务标题|Task title|Título de la tarea|Titre de la tâche|Aufgabentitel|Título da tarefa|Название задачи|タスクのタイトル|작업 제목
执行内容|Task instructions|Contenido de la tarea|Contenu de la tâche|Aufgabeninhalt|Conteúdo da tarefa|Содержание задачи|実行内容|작업 내용
任务类型|Task type|Tipo de tarea|Type de tâche|Aufgabentyp|Tipo de tarefa|Тип задачи|タスクの種類|작업 유형
单次|Once|Una vez|Une fois|Einmalig|Uma vez|Однократно|1回|한 번
每天|Daily|Cada día|Chaque jour|Täglich|Diariamente|Ежедневно|毎日|매일
每周|Weekly|Cada semana|Chaque semaine|Wöchentlich|Semanalmente|Еженедельно|毎週|매주
每月|Monthly|Cada mes|Chaque mois|Monatlich|Mensalmente|Ежемесячно|毎月|매월
固定间隔|Fixed interval|Intervalo fijo|Intervalle fixe|Festes Intervall|Intervalo fixo|Фиксированный интервал|固定間隔|고정 간격
执行时间（北京时间）|Execution time (China Standard Time)|Hora de ejecución (hora de China)|Heure d’exécution (heure de Chine)|Ausführungszeit (chinesische Standardzeit)|Horário de execução (horário da China)|Время выполнения (китайское время)|実行時刻（中国時間）|실행 시간(중국 표준시)
间隔秒数|Interval in seconds|Intervalo en segundos|Intervalle en secondes|Intervall in Sekunden|Intervalo em segundos|Интервал в секундах|間隔（秒）|간격(초)
执行时刻（北京时间，多个 HH:MM 以逗号分隔）|Execution times (China Standard Time; separate multiple HH:MM values with commas)|Horas de ejecución (hora de China; separa varios valores HH:MM con comas)|Heures d’exécution (heure de Chine ; séparez plusieurs valeurs HH:MM par des virgules)|Ausführungszeiten (chinesische Standardzeit; mehrere HH:MM-Werte durch Kommas trennen)|Horários de execução (horário da China; separe vários valores HH:MM por vírgulas)|Время выполнения (китайское время; несколько значений HH:MM разделяйте запятыми)|実行時刻（中国時間、複数のHH:MMはカンマ区切り）|실행 시각(중국 표준시, 여러 HH:MM 값은 쉼표로 구분)
生效日期|Start date|Fecha de inicio|Date de début|Startdatum|Data de início|Дата начала|開始日|시작일
结束日期|End date|Fecha de finalización|Date de fin|Enddatum|Data de término|Дата окончания|終了日|종료일
最大成功执行次数（留空不限）|Maximum successful runs (leave blank for unlimited)|Máximo de ejecuciones exitosas (en blanco para ilimitadas)|Nombre maximal d’exécutions réussies (laisser vide pour illimité)|Maximale erfolgreiche Ausführungen (leer lassen für unbegrenzt)|Máximo de execuções bem-sucedidas (em branco para ilimitado)|Максимум успешных запусков (пусто — без ограничений)|最大成功実行回数（空欄は無制限）|최대 성공 실행 횟수(비워 두면 무제한)
分页|Pagination|Paginación|Pagination|Seitennavigation|Paginação|Пагинация|ページネーション|페이지 나누기
共 {count} 个{label} · 每页最多 {size} 个|Total: {count} {label} · up to {size} per page|Total: {count} {label} · hasta {size} por página|Total : {count} {label} · {size} au maximum par page|Gesamt: {count} {label} · bis zu {size} pro Seite|Total: {count} {label} · até {size} por página|Всего: {count} {label} · до {size} на странице|合計 {count} {label} · 1ページ最大{size}件|총 {count} {label} · 페이지당 최대 {size}개
指导|Guidance|Indicaciones|Consignes|Anweisung|Orientação|Указание|ガイダンス|안내
取消子任务？|Cancel this subtask?|¿Cancelar esta subtarea?|Annuler cette sous-tâche ?|Diesen Unterauftrag abbrechen?|Cancelar esta subtarefa?|Отменить эту подзадачу?|このサブタスクをキャンセルしますか？|이 하위 작업을 취소할까요?
`
