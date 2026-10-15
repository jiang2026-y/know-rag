/**
 * 简洁问答主页：提供登录、会话创建、POST SSE 消费、引用和错误展示。
 * 定义 HomePage、requestJson、consumeSse、parseSseBlock 与若干前端类型；
 * 所有身份与权限均由 FastAPI 服务端 Session 处理，浏览器只调用同源代理路径。
 */

"use client";

import { FormEvent, useMemo, useState } from "react";

type LoginKind = "staff" | "customer";

type Principal = {
  principal_type: "CUSTOMER" | "STAFF";
  principal_id: string;
  job: string | null;
  dept_id: number | null;
};

type LoginResponse = {
  principal: Principal;
  expires_in_seconds: number;
};

type ConversationCreatedResponse = {
  conversation_id: string;
  first_message_id: string;
  title: string;
  title_pending: boolean;
};

type DocumentUploadTaskAccepted = {
  task_id: string;
  document_id: number;
  state: "queued" | "processing" | "completed" | "failed";
  document_status: string;
  status_url: string;
};

type DocumentUploadTaskStatus = DocumentUploadTaskAccepted & {
  original_filename: string;
  error: string | null;
  attempt_count: number;
};

type ReviewDocument = {
  document_id: number;
  title: string;
  status: string;
  accessible_by: "VISITOR" | "STAFF" | "LEADER" | null;
  upload_user: string | null;
  updated_at: string;
};

type ReviewQueueResponse = { documents: ReviewDocument[] };

type Citation = {
  citation_id: string;
  source_name?: string;
  section?: string | null;
  source_type?: string;
  document_id?: number | null;
  chunk_id?: string;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  streaming?: boolean;
  serverMessageId?: string;
  reaction?: "LIKE" | "DISLIKE";
};

type SseEvent = {
  event: "progress" | "query_rewrite" | "answer_delta" | "citation" | "error" | "done";
  delta?: string;
  citation?: Citation;
  code?: string;
  message?: string;
  status?: "completed" | "error";
  answer_message_id?: string | null;
  stage?: string;
  original_query?: string;
  rewritten_query?: string;
  status_code?: string;
};

type FeedbackResponse = {
  message_id: string;
  reaction: "LIKE" | "DISLIKE";
  expires_at: string;
  trace_id: string;
};

const apiPath = (path: string) => `/backend${path}`;
const v1 = (path: string) => `/api/v1${path}`;

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(apiPath(path), {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init.headers },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `请求失败（HTTP ${response.status}）`);
  }
  return (await response.json()) as T;
}

async function requestForm<T>(path: string, form: FormData): Promise<T> {
  const response = await fetch(apiPath(path), {
    method: "POST",
    body: form,
    credentials: "include",
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `请求失败（HTTP ${response.status}）`);
  }
  return (await response.json()) as T;
}

function parseSseBlock(block: string): SseEvent | null {
  const lines = block.split("\n");
  const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
  const data = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim())
    .join("\n");
  if (!event || !data) {
    return null;
  }
  try {
    return { event: event as SseEvent["event"], ...(JSON.parse(data) as object) };
  } catch {
    return null;
  }
}

async function consumeSse(
  response: Response,
  onEvent: (event: SseEvent) => void,
): Promise<void> {
  if (!response.body) {
    throw new Error("浏览器未获得回答流。");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const event = parseSseBlock(block);
      if (event) {
        onEvent(event);
        if (event.event === "answer_delta") {
          await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
        }
      }
    }
    if (done) {
      break;
    }
  }
}

export default function HomePage() {
  const [loginKind, setLoginKind] = useState<LoginKind>("staff");
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [question, setQuestion] = useState("");
  const [conversation, setConversation] = useState<ConversationCreatedResponse | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [feedbackMessageId, setFeedbackMessageId] = useState<string | null>(null);
  const [documentFile, setDocumentFile] = useState<File | null>(null);
  const [documentTask, setDocumentTask] = useState<DocumentUploadTaskStatus | null>(null);
  const [splitType, setSplitType] = useState("TITLE");
  const [chunkSize, setChunkSize] = useState("");
  const [overlap, setOverlap] = useState("");
  const [titleLevel, setTitleLevel] = useState("");
  const [separator, setSeparator] = useState("");
  const [splitRegex, setSplitRegex] = useState("");
  const [reviewDocuments, setReviewDocuments] = useState<ReviewDocument[]>([]);
  const [reviewScope, setReviewScope] = useState<"VISITOR" | "STAFF" | "LEADER">("STAFF");
  const [documentBusy, setDocumentBusy] = useState(false);
  const [documentNotice, setDocumentNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("请先登录，再开始提问。");

  const accountLabel = loginKind === "staff" ? "工号" : "手机号";
  const canSubmitQuestion = Boolean(principal && question.trim() && !busy);
  const isStaff = principal?.principal_type === "STAFF";
  const isLeader = isStaff && principal?.job === "leader";
  const principalLabel = useMemo(() => {
    if (!principal) {
      return "未登录";
    }
    return principal.principal_type === "STAFF"
      ? `${principal.principal_id} · ${principal.job ?? "staff"}`
      : principal.principal_id;
  }, [principal]);

  async function loadReviewQueue() {
    const result = await requestJson<ReviewQueueResponse>(v1("/documents/review-queue"), {
      method: "GET",
    });
    setReviewDocuments(result.documents);
  }

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setNotice("正在验证账号…");
    try {
      const body = loginKind === "staff"
        ? { emp_id: account.trim(), password }
        : { phone: account.trim(), password };
      const result = await requestJson<LoginResponse>(v1(`/auth/${loginKind}/sessions`), {
        method: "POST",
        body: JSON.stringify(body),
      });
      setPrincipal(result.principal);
      setConversation(null);
      setMessages([]);
      setPassword("");
      setNotice("登录成功，可以开始提问。");
      if (result.principal.principal_type === "STAFF") {
        try {
          await loadReviewQueue();
        } catch (error) {
          setDocumentNotice(error instanceof Error ? error.message : "无法读取文档队列。");
        }
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "登录失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  async function handleLogout() {
    setBusy(true);
    try {
      await fetch(apiPath(v1("/auth/session")), { method: "DELETE", credentials: "include" });
      setPrincipal(null);
      setConversation(null);
      setMessages([]);
      setDocumentFile(null);
      setDocumentTask(null);
      setReviewDocuments([]);
      setDocumentNotice("");
      setNotice("已退出登录。");
    } finally {
      setBusy(false);
    }
  }

  async function handleDocumentUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!documentFile || !isStaff || documentBusy) {
      return;
    }
    setDocumentBusy(true);
    setDocumentNotice("正在提交文档处理任务…");
    try {
      const form = new FormData();
      form.append("file", documentFile);
      // 留空的参数交给服务端默认值（TITLE + 1000 字/100 重叠），避免小分片触发校验失败。
      form.append("split_type", splitType);
      if (chunkSize.trim()) form.append("chunk_size", chunkSize.trim());
      if (overlap.trim()) form.append("overlap", overlap.trim());
      if (titleLevel.trim()) form.append("title_level", titleLevel.trim());
      if (separator) form.append("separator", separator);
      if (splitRegex.trim()) form.append("regex", splitRegex.trim());
      const result = await requestForm<DocumentUploadTaskAccepted>(v1("/documents/upload"), form);
      setDocumentTask({ ...result, original_filename: documentFile.name, error: null, attempt_count: 0 });
      setDocumentFile(null);
      setDocumentNotice(`已创建处理任务，文档编号 ${result.document_id}。`);
    } catch (error) {
      setDocumentNotice(error instanceof Error ? error.message : "文档上传失败，请稍后重试。");
    } finally {
      setDocumentBusy(false);
    }
  }

  async function refreshDocumentTask() {
    if (!documentTask || documentBusy) {
      return;
    }
    setDocumentBusy(true);
    try {
      const result = await requestJson<DocumentUploadTaskStatus>(
        v1(`/documents/tasks/${encodeURIComponent(documentTask.task_id)}`),
        { method: "GET" },
      );
      setDocumentTask(result);
      setDocumentNotice(`任务当前状态：${result.state} / ${result.document_status}`);
      if (result.document_status === "PENDING_REVIEW" || result.document_status === "REJECTED") {
        await loadReviewQueue();
      }
    } catch (error) {
      setDocumentNotice(error instanceof Error ? error.message : "无法读取任务状态。");
    } finally {
      setDocumentBusy(false);
    }
  }

  async function retryDocumentTask() {
    if (!documentTask || documentBusy) {
      return;
    }
    setDocumentBusy(true);
    try {
      const result = await requestJson<DocumentUploadTaskAccepted>(
        v1(`/documents/tasks/${encodeURIComponent(documentTask.task_id)}/retry`),
        { method: "POST", body: "{}" },
      );
      setDocumentTask({ ...result, original_filename: documentTask.original_filename, error: null, attempt_count: documentTask.attempt_count });
      setDocumentNotice("任务已重新进入队列。");
    } catch (error) {
      setDocumentNotice(error instanceof Error ? error.message : "任务重试失败。");
    } finally {
      setDocumentBusy(false);
    }
  }

  async function reviewDocument(documentId: number, decision: "APPROVE" | "REJECT") {
    if (!isLeader || documentBusy) {
      return;
    }
    setDocumentBusy(true);
    try {
      const body = decision === "APPROVE"
        ? { decision, accessible_by: reviewScope }
        : { decision };
      await requestJson(v1(`/documents/${documentId}/review`), {
        method: "POST",
        body: JSON.stringify(body),
      });
      setDocumentNotice(decision === "APPROVE" ? "文档已通过审核并开始向量化。" : "文档已驳回，不会进入检索。");
      await loadReviewQueue();
    } catch (error) {
      setDocumentNotice(error instanceof Error ? error.message : "文档审核失败。");
    } finally {
      setDocumentBusy(false);
    }
  }

  async function handleQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = question.trim();
    if (!content || !principal || busy) {
      return;
    }
    setBusy(true);
    setQuestion("");
    setNotice("正在生成回答…");
    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: "user", content };
    const assistantMessageId = crypto.randomUUID();
    setMessages((current) => [...current, userMessage, {
      id: assistantMessageId,
      role: "assistant",
      content: "",
      streaming: true,
    }]);

    try {
      let activeConversation = conversation;
      let streamPayload: Record<string, string>;
      if (!activeConversation) {
        activeConversation = await requestJson<ConversationCreatedResponse>(v1("/chat/conversations"), {
          method: "POST",
          body: JSON.stringify({ first_message: content }),
        });
        setConversation(activeConversation);
        streamPayload = { message_id: activeConversation.first_message_id };
      } else {
        streamPayload = { message: content };
      }
      const response = await fetch(
        apiPath(v1(`/chat/conversations/${encodeURIComponent(activeConversation.conversation_id)}/messages:stream`)),
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(streamPayload),
        },
      );
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(payload?.detail ?? `回答请求失败（HTTP ${response.status}）`);
      }
      let streamErrorMessage: string | null = null;
      await consumeSse(response, (sseEvent) => {
        if (sseEvent.event === "progress") {
          setNotice(sseEvent.message ?? "正在优化您的问题…");
        }
        if (sseEvent.event === "query_rewrite") {
          setNotice(
            sseEvent.rewritten_query && sseEvent.original_query && sseEvent.rewritten_query !== sseEvent.original_query
              ? `问题已优化：${sseEvent.rewritten_query}`
              : "问题已完成检索优化。",
          );
        }
        if (sseEvent.event === "answer_delta") {
          setMessages((current) => current.map((message) => (
            message.id === assistantMessageId
              ? { ...message, content: message.content + (sseEvent.delta ?? "") }
              : message
          )));
        }
        if (sseEvent.event === "citation" && sseEvent.citation) {
          const citation = sseEvent.citation;
          setMessages((current) => current.map((message) => (
            message.id === assistantMessageId
              ? { ...message, citations: [...(message.citations ?? []), citation] }
              : message
          )));
        }
        if (sseEvent.event === "error") {
          streamErrorMessage = sseEvent.message ?? "回答服务暂不可用。";
          setNotice(streamErrorMessage);
          setMessages((current) => current.map((message) => (
            message.id === assistantMessageId ? { ...message, streaming: false } : message
          )));
        }
        if (sseEvent.event === "done") {
          setMessages((current) => current.map((message) => (
            message.id === assistantMessageId
              ? { ...message, streaming: false, serverMessageId: sseEvent.answer_message_id ?? undefined }
              : message
          )));
        }
      });
      if (!streamErrorMessage) {
        setNotice("回答完成。");
      }
    } catch (error) {
      setMessages((current) => current.map((message) => (
        message.id === assistantMessageId && !message.content
          ? { ...message, content: "本次回答未完成，请稍后重试。", streaming: false }
          : message.id === assistantMessageId ? { ...message, streaming: false } : message
      )));
      setNotice(error instanceof Error ? error.message : "回答请求失败，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  async function submitFeedback(message: ChatMessage, reaction: "LIKE" | "DISLIKE") {
    if (!message.serverMessageId || feedbackMessageId) {
      return;
    }
    setFeedbackMessageId(message.serverMessageId);
    try {
      const result = await requestJson<FeedbackResponse>(
        v1(`/messages/${encodeURIComponent(message.serverMessageId)}/feedback`),
        {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({ reaction }),
        },
      );
      setMessages((current) => current.map((currentMessage) => (
        currentMessage.id === message.id ? { ...currentMessage, reaction: result.reaction } : currentMessage
      )));
      setNotice(result.reaction === "LIKE" ? "感谢你的赞。" : "已收到你的反馈，我们会持续改进。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "反馈提交失败，请稍后重试。");
    } finally {
      setFeedbackMessageId(null);
    }
  }

  return (
    <main className="page-shell">
      <header className="page-header">
        <div>
          <p className="eyebrow">LLMCOPY · P0</p>
          <h1>汽车知识问答</h1>
        </div>
        <div className="session-state">
          <span>{principalLabel}</span>
          {principal && <button className="text-button" onClick={handleLogout} disabled={busy}>退出</button>}
        </div>
      </header>

      {!principal && (
        <section className="login-card" aria-labelledby="login-title">
          <div>
            <p className="eyebrow">身份验证</p>
            <h2 id="login-title">登录后开始提问</h2>
            <p>员工使用工号，顾客使用手机号。身份和权限始终由服务端 Session 校验。</p>
          </div>
          <form onSubmit={handleLogin} className="login-form">
            <div className="segmented-control" role="group" aria-label="登录身份">
              <button type="button" className={loginKind === "staff" ? "selected" : ""} onClick={() => setLoginKind("staff")}>员工</button>
              <button type="button" className={loginKind === "customer" ? "selected" : ""} onClick={() => setLoginKind("customer")}>顾客</button>
            </div>
            <label>{accountLabel}<input value={account} onChange={(event) => setAccount(event.target.value)} required /></label>
            <label>密码<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
            <button className="primary-button" disabled={busy}>{busy ? "处理中…" : "登录"}</button>
          </form>
        </section>
      )}

      <section className="chat-card" aria-live="polite">
        <div className="chat-heading">
          <div>
            <p className="eyebrow">受控问答</p>
            <h2>{conversation?.title ?? "新对话"}</h2>
          </div>
          <span className="status-dot">{busy ? "处理中" : "就绪"}</span>
        </div>
        <div className="notice" role="status">{notice}</div>
        <div className="message-list">
          {messages.length === 0 && <p className="empty-state">登录后输入汽车相关问题；非汽车问题会走通用回答，不访问企业知识。</p>}
          {messages.map((message) => (
            <article className={`message ${message.role}`} key={message.id}>
              <span className="message-role">{message.role === "user" ? "你" : "助手"}</span>
              <p>{message.content || "正在思考…"}{message.streaming && <span className="stream-cursor" aria-label="正在流式生成">▍</span>}</p>
              {message.citations && message.citations.length > 0 && (
                <div className="citations">
                  {message.citations.map((citation) => (
                    <span key={citation.citation_id}>
                      引用：{citation.source_name ?? `文档 ${citation.document_id ?? "未知"}`}
                      {citation.section ? ` · ${citation.section}` : ""}
                    </span>
                  ))}
                </div>
              )}
              {message.role === "assistant" && message.serverMessageId && !message.streaming && (
                <div className="feedback-actions" aria-label="回答评价">
                  <span>这条回答有帮助吗？</span>
                  <button
                    type="button"
                    className={message.reaction === "LIKE" ? "feedback-button selected" : "feedback-button"}
                    onClick={() => void submitFeedback(message, "LIKE")}
                    disabled={feedbackMessageId !== null}
                  >
                    赞
                  </button>
                  <button
                    type="button"
                    className={message.reaction === "DISLIKE" ? "feedback-button selected" : "feedback-button"}
                    onClick={() => void submitFeedback(message, "DISLIKE")}
                    disabled={feedbackMessageId !== null}
                  >
                    踩
                  </button>
                </div>
              )}
            </article>
          ))}
        </div>
        <form className="question-form" onSubmit={handleQuestion}>
          <label className="sr-only" htmlFor="question">输入问题</label>
          <textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="例如：自适应巡航怎么开启？" maxLength={4000} disabled={!principal || busy} />
          <button className="primary-button" disabled={!canSubmitQuestion}>{busy ? "回答中…" : "发送"}</button>
        </form>
      </section>

      {isStaff && (
        <section className="document-card" aria-labelledby="document-title">
          <div className="chat-heading">
            <div>
              <p className="eyebrow">知识管理</p>
              <h2 id="document-title">上传与审核</h2>
            </div>
            <span className="status-dot">{isLeader ? "领导审核" : "员工上传"}</span>
          </div>
          <p className="document-description">支持 PDF、DOC、DOCX、CSV 与 Excel。文档解析完成后由领导决定发布范围；上传者不能自行设定权限。</p>
          <form className="upload-form" onSubmit={handleDocumentUpload}>
            <input
              type="file"
              accept=".pdf,.doc,.docx,.csv,.xls,.xlsx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(event) => setDocumentFile(event.target.files?.[0] ?? null)}
              disabled={documentBusy}
              required
            />
            <label className="split-field">
              切片策略
              <select
                value={splitType}
                onChange={(event) => setSplitType(event.target.value)}
                disabled={documentBusy}
              >
                <option value="TITLE">TITLE（按标题父子切分）</option>
                <option value="SMART">SMART（标题切分，重叠 10%）</option>
                <option value="LENGTH">LENGTH（按长度平铺切分）</option>
                <option value="REGEX">REGEX（按正则切分）</option>
                <option value="SEPARATOR">SEPARATOR（按分隔符切分）</option>
              </select>
            </label>
            {splitType === "REGEX" && (
              <label className="split-field">
                正则表达式
                <input
                  type="text"
                  value={splitRegex}
                  onChange={(event) => setSplitRegex(event.target.value)}
                  placeholder="例如：\n\n"
                  disabled={documentBusy}
                />
              </label>
            )}
            {splitType === "SEPARATOR" && (
              <label className="split-field">
                分隔符
                <input
                  type="text"
                  value={separator}
                  onChange={(event) => setSeparator(event.target.value)}
                  placeholder="例如：；"
                  disabled={documentBusy}
                />
              </label>
            )}
            {splitType !== "SMART" && (
              <label className="split-field">
                分片字符数
                <input
                  type="number"
                  min={1}
                  value={chunkSize}
                  onChange={(event) => setChunkSize(event.target.value)}
                  placeholder="默认 1000"
                  disabled={documentBusy}
                />
              </label>
            )}
            {splitType !== "SMART" && (
              <label className="split-field">
                重叠字符数
                <input
                  type="number"
                  min={0}
                  value={overlap}
                  onChange={(event) => setOverlap(event.target.value)}
                  placeholder="默认 100"
                  disabled={documentBusy}
                />
              </label>
            )}
            {splitType === "TITLE" && (
              <label className="split-field">
                标题层级
                <input
                  type="number"
                  min={1}
                  max={6}
                  value={titleLevel}
                  onChange={(event) => setTitleLevel(event.target.value)}
                  placeholder="默认 6"
                  disabled={documentBusy}
                />
              </label>
            )}
            <button className="primary-button" disabled={!documentFile || documentBusy}>
              {documentBusy ? "处理中…" : "上传文档"}
            </button>
          </form>
          {documentNotice && <div className="notice" role="status">{documentNotice}</div>}
          {documentTask && (
            <div className="task-summary">
              <span>{documentTask.original_filename}</span>
              <span>任务：{documentTask.state} / 文档：{documentTask.document_status}</span>
              <span>尝试次数：{documentTask.attempt_count}</span>
              {documentTask.error && <span className="task-error">失败原因：{documentTask.error}</span>}
              <div className="inline-actions">
                <button type="button" className="secondary-button" onClick={refreshDocumentTask} disabled={documentBusy}>刷新状态</button>
                {documentTask.state === "failed" && <button type="button" className="secondary-button" onClick={retryDocumentTask} disabled={documentBusy}>重试任务</button>}
              </div>
            </div>
          )}
          <div className="review-heading">
            <h3>{isLeader ? "待审核文档" : "我的待审核与已驳回文档"}</h3>
            <button type="button" className="text-button" onClick={() => void loadReviewQueue()} disabled={documentBusy}>刷新列表</button>
          </div>
          {isLeader && (
            <label className="review-scope">通过时的可见范围
              <select value={reviewScope} onChange={(event) => setReviewScope(event.target.value as "VISITOR" | "STAFF" | "LEADER")} disabled={documentBusy}>
                <option value="VISITOR">VISITOR（公开）</option>
                <option value="STAFF">STAFF（全体员工）</option>
                <option value="LEADER">LEADER（仅领导）</option>
              </select>
            </label>
          )}
          {reviewDocuments.length === 0 ? (
            <p className="empty-state document-empty">当前没有可显示的文档。</p>
          ) : (
            <div className="review-list">
              {reviewDocuments.map((document) => (
                <article className="review-item" key={document.document_id}>
                  <div>
                    <strong>{document.title}</strong>
                    <p>编号 {document.document_id} · 状态 {document.status} · 上传人 {document.upload_user ?? "未知"}</p>
                  </div>
                  {isLeader && document.status === "PENDING_REVIEW" && (
                    <div className="inline-actions">
                      <button type="button" className="secondary-button" onClick={() => void reviewDocument(document.document_id, "APPROVE")} disabled={documentBusy}>通过</button>
                      <button type="button" className="danger-button" onClick={() => void reviewDocument(document.document_id, "REJECT")} disabled={documentBusy}>驳回</button>
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>
      )}
    </main>
  );
}
