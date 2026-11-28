"use strict";
const view = document.querySelector("#view");
const feedback = document.querySelector("#feedback");
let generation = 0;
const timestamp = (value) => value == null ? "Unknown" : new Date(typeof value === "number" ? value * 1000 : value).toLocaleString();
const money = (item) => item.price == null ? "Price unknown" : Number(item.price) === 0 ? `Free · ${item.currency || "currency unknown"}` : `${item.price} ${item.currency || "(currency unknown)"}`;
const sourceName = (source) => source === "fixture" ? "Simulated fixture" : source === "alert" ? "Forwarded alert" : "Manual import";
const states = ["new", "saved", "dismissed", "contacted manually"];

function node(tag, props = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(props)) {
    if (name.startsWith("on")) element.addEventListener(name.slice(2), value);
    else if (name === "className") element.className = value;
    else if (value !== false && value != null) element.setAttribute(name, value === true ? "" : value);
  }
  element.append(...children.flat().filter((child) => child != null && child !== false));
  return element;
}
const button = (label, action, appearance = "") => node("button", {type:"button", className:appearance, onclick:action}, label);
const heading = (title, subtitle, ...actions) => node("div", {className:"heading"},
  node("div", {}, node("h1", {}, title), node("p", {}, subtitle)), node("div", {className:"actions"}, ...actions));
const empty = (message) => node("div", {className:"card"}, message);
const chip = (text, warning = false) => node("span", {className:`chip ${warning ? "warning" : ""}`}, text);
const link = (url) => node("a", {className:"button", href:url, target:"_blank", rel:"noopener noreferrer"}, "Open listing");

async function api(path, method = "GET", payload) {
  const response = await fetch(path, {method, headers: payload === undefined ? {} : {"Content-Type":"application/json"},
    body:payload === undefined ? undefined : JSON.stringify(payload)});
  if (response.status === 204) return null;
  const data = await response.json();
  if (!response.ok) throw new Error([data.detail, ...(data.errors || []).map((e) => `${e.field} · ${e.message}`)].join(" — "));
  return data;
}
async function action(work) {
  try { feedback.textContent = ""; await work(); }
  catch (error) { feedback.textContent = error.message; }
}
function input(name, label, value = "", options = {}) {
  const tag = options.multiline ? "textarea" : "input";
  const control = node(tag, {name, ...Object.fromEntries(Object.entries(options).filter(([key]) => key !== "multiline"))});
  if (options.type === "checkbox") control.checked = !!value;
  else control.value = value ?? "";
  return node("label", {}, label, control);
}
function select(name, label, options, value) {
  const control = node("select", {name}, options.map(([id, text]) => node("option", {value:id}, text)));
  control.value = value;
  return node("label", {}, label, control);
}
function form(fields, label, handler, ...actions) {
  const element = node("form", {onsubmit:(event) => {
    event.preventDefault();
    const submit = event.submitter;
    if (submit) submit.disabled = true;
    void action(() => handler(new FormData(element))).finally(() => { if (submit) submit.disabled = false; });
  }}, node("div", {className:"grid"}, fields), node("div", {className:"actions"}, node("button", {className:"primary"}, label), ...actions));
  return element;
}
const section = (title, children, fallback) => node("section", {}, node("h2", {}, title), children.length ? children : empty(fallback));

async function overview() {
  const [summary, sources, ready] = await Promise.all([api("/api/overview"), api("/api/sources"), api("/health/ready")]);
  const metrics = [["All stored listings", summary.total_listings], ["Enabled watchlists", summary.enabled], ["Confirmed listings", summary.confirmed],
    ["Last successful poll", timestamp(summary.last_sync)], ["Failed runs retained", summary.failed]];
  return [heading("Deal overview", "Scheduled polls use simulated listings. Add real listings by manual import or forwarded alert.",
    button("View all listings", () => openListings(), "primary"),
    button("Refresh", () => show("overview")), button("Add demo watchlists", () => action(async () => {
      const result = await api("/api/demo", "POST");
      await show("watchlists");
      feedback.textContent = result.length ? "Demo watchlists added. Fixture data is simulated." : "Demo watchlists already exist.";
    }))),
    node("div", {className:"grid"}, metrics.map(([label, value]) => node("div", {className:"card stat"}, node("small", {}, label), node("strong", {}, value)))),
    section("Sources and system health", [node("div", {className:"card"}, chip(`Database ${ready.status}`),
      sources.map((source) => node("div", {}, node("strong", {}, sourceName(source.id)), " ",
        chip(source.state?.paused ? "Paused" : source.polling ? "Scheduled" : "Event-driven", !!source.state?.paused),
        node("p", {}, source.description),
        source.state?.paused ? button("Resume fixture", () => action(async () => { await api("/api/sources/fixture/resume", "POST"); await show("overview"); })) : null)))], "")];
}
function editor(item = {}) {
  const txt = (name, label, fallback = "") => input(name, label, item[name] ?? fallback);
  const num = (name, label, fallback, options = {}) =>
    input(name, label, item[name] ?? fallback, {type:"number", min:"0", ...options});
  const check = (name, label, fallback = true) => input(name, label, item[name] ?? fallback, {type:"checkbox"});
  const list = (name, label, fallback = []) => input(name, label, (item[name] ?? fallback).join(", "));
  return form([
    input("name", "Name", item.name, {required:true, maxlength:"160"}), txt("phrase", "Search phrase"), txt("currency", "Currency", "CAD"),
    num("max_price", "Maximum price", "", {step:".01", required:true}), num("min_price", "Minimum price", "", {step:".01"}),
    txt("location", "Location (optional)", item.id ? "" : "Halifax"), num("radius_km", "Radius (km)", "", {step:".1"}),
    num("max_age_hours", "Maximum age (hours)", "", {min:"1"}), num("priority", "Priority (0–10)", 0, {max:"10"}),
    num("interval_minutes", "Polling interval (minutes)", 5, {min:"1", max:"1440", required:true}),
    list("include", "All include keywords (comma-separated)"),
    list("exclude", "Exclude any (comma-separated)", ["for parts", "broken", "empty box", "wanted"]),
    list("conditions", "Allowed conditions (comma-separated)"), list("weights", "Score weights · price, relevance, freshness, condition", [45,30,15,10]),
    select("initial", "Initial fixture results", [["silent","Silent baseline"],["digest","Digest"],["alert","Individual alerts"]], item.initial ?? "silent"),
    ...["fixture","manual","alert"].map((source) => input(source, sourceName(source), (item.sources ?? ["fixture"]).includes(source), {type:"checkbox"})),
    check("enabled","Enabled"), check("notify_new","New match alerts"), check("notify_drops","Price-drop alerts"),
    check("drafts","Include seller drafts"), check("review_uncertain","Show uncertain matches")
  ], item.id ? "Save changes" : "Create watchlist", async (data) => {
    const split = (key) => data.get(key).split(",").map((value) => value.trim()).filter(Boolean);
    const optional = (key) => data.get(key).trim() || null;
    const payload = {name:data.get("name"), phrase:data.get("phrase"), currency:data.get("currency").trim().toUpperCase(),
      max_price:optional("max_price"), min_price:optional("min_price"), location:optional("location"),
      radius_km:optional("radius_km"), max_age_hours:optional("max_age_hours"), priority:data.get("priority"),
      interval_minutes:data.get("interval_minutes"), include:split("include"), exclude:split("exclude"), conditions:split("conditions"),
      weights:split("weights").map(Number), initial:data.get("initial"), sources:["fixture","manual","alert"].filter((source) => data.has(source))};
    for (const key of ["enabled","notify_new","notify_drops","drafts","review_uncertain"]) payload[key] = data.has(key);
    await api(`/api/watchlists${item.id ? "/" + item.id : ""}`, item.id ? "PUT" : "POST", payload);
    await show("watchlists"); feedback.textContent = "Watchlist saved.";
  });
}
async function watchlists() {
  const items = await api("/api/watchlists");
  return [heading("Watchlists", "Choose sources, matching rules, and notification preferences.", button("Refresh", () => show("watchlists"))),
    node("details", {open:!items.length}, node("summary", {}, "Create a watchlist"), editor()),
    ...items.map((item) => node("article", {className:"card"}, node("h2", {}, item.name),
      chip(item.enabled ? "Enabled" : "Disabled", !item.enabled), item.sources.map((source) => chip(sourceName(source))),
      node("p", {}, `Up to ${item.max_price} ${item.currency} · ${item.due == null ? "No scheduled poll" : "Next poll " + timestamp(item.due)}`),
      node("p", {}, `Last successful poll · ${timestamp(item.last_success)}`),
      item.last_error ? node("p", {className:"error"}, item.last_error) : null,
      node("details", {}, node("summary", {}, "Edit watchlist"), editor(item)),
      node("div", {className:"actions"},
        item.enabled && item.sources.includes("fixture") ? button("Run now", () => action(async () => {
          const result = await api(`/api/watchlists/${item.id}/run`, "POST");
          feedback.replaceChildren(`Run ${result.status}. `, button("Open Activity", () => show("activity")));
        })) : null,
        button("Delete", () => action(async () => {
          if (!confirm(`Delete watchlist “${item.name}”?`)) return;
          await api(`/api/watchlists/${item.id}`, "DELETE"); await show("watchlists");
        })))))];
}
const defaultFeedFilters = {source:"", status:"", state:"", sort:"recent", watchlist_id:"", offset:0};
const feedFilters = {...defaultFeedFilters};
let selectedRun = null;

function openListings(run = null) {
  selectedRun = run;
  Object.assign(feedFilters, defaultFeedFilters);
  return show("listings");
}

function importForm() {
  return form([
    input("url", "Listing URL", "", {type:"url", required:true}),
    input("title", "Title"), input("description", "Description", "", {multiline:true, maxlength:"4000"}),
    input("price", "Price", "", {type:"number", min:"0", step:".01"}), input("currency", "Currency"),
    input("location","Location"), input("condition","Condition"),
    input("distance_km","Distance (km)","",{type:"number",min:"0",step:".1"}), input("distance_from","Distance from"),
    input("listed_at","Posted at (local time)","",{type:"datetime-local"})
  ], "Import listing", async (data) => {
    const body = Object.fromEntries([...data].filter(([, value]) => value.trim()));
    if (body.listed_at) body.listed_at = new Date(body.listed_at).toISOString();
    if (body.currency) body.currency = body.currency.toUpperCase();
    const result = await api("/api/ingestion/manual", "POST", body);
    selectedRun = null;
    Object.assign(feedFilters, defaultFeedFilters);
    await detail(result.listing_ids[0]);
    feedback.textContent = "Listing imported.";
  });
}
async function listings() {
  const run = selectedRun;
  const offset = feedFilters.offset;
  const query = new URLSearchParams(Object.entries(feedFilters).filter(([, value]) => value !== ""));
  if (run) query.set("run_id", run.id);
  const [data, watches] = await Promise.all([api("/api/listings?" + query), api("/api/watchlists")]);
  const filtered = Boolean(feedFilters.source || feedFilters.status || feedFilters.state || feedFilters.watchlist_id);
  const description = run ? `${run.name} · ${sourceName(run.source)} · ${timestamp(run.started)}. Prices and match labels reflect the latest stored details.`
    : "All stored listings, including unmatched and dismissed items.";
  const emptyMessage = filtered ? "No listings match these filters." : run ? "This run returned no listings."
    : "No listings have been collected yet. Run a fixture watchlist or import a listing below.";
  const filters = node("div", {className:"filters", onchange:(event) => {
    feedFilters[event.target.name] = event.target.value; feedFilters.offset = 0; void show("listings");
  }},
    select("source","Source",[["","All"],...["fixture","manual","alert"].map((s) => [s,sourceName(s)])],feedFilters.source),
    select("status","Match",[["","All"],["confirmed","Confirmed"],["uncertain","Uncertain"],["unmatched","Unmatched"]],feedFilters.status),
    select("state","State",[["","All"],...states.map((s) => [s,s])],feedFilters.state),
    select("watchlist_id","Watchlist",[["","All"],...watches.map((watch) => [watch.id,watch.name])],feedFilters.watchlist_id),
    select("sort","Sort",[["recent","Recent"],["price","Lowest price"],["score","Highest score"]],feedFilters.sort));
  return [heading(run ? "Run listings" : "Listings", description, button("Refresh", () => show("listings")),
    run ? button("View all listings", () => openListings()) : null,
    run ? button("Back to Activity", () => show("activity")) : null),
    run ? null : node("details", {}, node("summary", {}, "Import listing manually"), importForm()), filters,
    filtered ? button("Clear filters", () => openListings(run)) : null,
    node("p", {role:"status"}, data.items.length ? `Showing ${offset + 1}–${offset + data.items.length} of ${data.total} listings` : `${data.total} listings`),
    ...(data.items.length ? data.items.map((item) => node("article", {className:"row"},
      node("div", {}, node("h3", {}, item.title || "Untitled listing"), node("p", {}, `${money(item)} · ${item.location || "Location unknown"}`),
        chip(sourceName(item.source)), chip(item.state),
        item.matches.length ? item.matches.map((m) => chip(`${m.watchlist} · ${m.status} · ${m.score}`, m.status === "uncertain")) : chip("Unmatched")),
      button("View details", () => action(() => detail(item.id))))) : [empty(emptyMessage)]),
    node("div", {className:"actions"}, feedFilters.offset ? button("Previous page", () => { feedFilters.offset = Math.max(0, feedFilters.offset - 30); void show("listings"); }) : null,
      data.next_offset != null ? button("Next page", () => { feedFilters.offset = data.next_offset; void show("listings"); }) : null)];
}
async function detail(id) {
  const renderId = ++generation;
  const [item, history] = await Promise.all([api(`/api/listings/${id}`), api(`/api/listings/${id}/history`)]);
  if (renderId !== generation) return;
  const base = `/api/listings/${id}`;
  const draftForm = form([input("message", "Seller message (review and send manually)", item.draft ?? "", {multiline:true, maxlength:"1200"})],
    "Save draft", async (data) => { await api(base + "/message-draft", "POST", {message:data.get("message")}); feedback.textContent = "Draft saved."; },
    button("Generate draft", () => action(async () => {
      const result = await api(base + "/message-draft", "POST", {});
      draftForm.elements.message.value = result.message;
    })), button("Copy draft", () => action(async () => {
      await navigator.clipboard.writeText(draftForm.elements.message.value); feedback.textContent = "Draft copied.";
    })));
  const details = node("div", {id:"pane-details", className:"pane", role:"tabpanel", "aria-labelledby":"tab-details"},
    node("div", {className:"card"}, node("p", {}, item.description || "Description unknown"),
      node("p", {}, `Location · ${item.location || "Unknown"} | Condition · ${item.condition || "Unknown"}`),
      node("p", {}, `Distance · ${item.distance_km == null ? "Unknown" : item.distance_km + " km from " + (item.distance_from || "unknown origin")}`),
      node("p", {}, `Posted · ${timestamp(item.listed_at)} | First seen · ${timestamp(item.first_seen)} | Last seen · ${timestamp(item.last_seen)}`),
      select("state","Listing state",states.map((state) => [state,state]),item.state)),
    section("Match details", item.matches.map((m) => node("div", {className:"card"}, node("h3", {}, m.watchlist),
      chip(`${m.status} · ${m.score}/100`, m.status === "uncertain"), node("p", {}, m.reasons.join(" · ")),
      node("p", {}, m.unknown.length ? "Unknown · " + m.unknown.join(", ") : ""),
      node("p", {}, Object.entries(m.breakdown).map(([key, points]) => `${key} ${points}`).join(" · ")))), "No watchlist match."),
    section("Price and revision history", history.map((entry) => node("div", {className:"row"},
      node("strong", {}, money(entry)), entry.title || "Untitled", timestamp(entry.observed))), "No retained revisions."),
    node("h2", {}, "Seller message"),
    node("p", {}, item.source === "fixture" ? "You can try the draft tools on this simulated listing."
      : "Copy the draft, open the listing, and send it yourself on the marketplace."),
    draftForm, await evidencePanel(item, renderId), await listingProjects(item));
  if (renderId !== generation) return;
  const chat = await chatPanel(item, renderId);
  if (renderId !== generation) return;
  const tabs = node("div", {className:"pane-tabs", role:"tablist", "aria-label":"Listing workspace"},
    ...[["details","Details"],["chat","Chat"]].map(([key, label]) => node("button", {type:"button", role:"tab", id:`tab-${key}`,
      "aria-controls":`pane-${key}`, "aria-selected": String(key === "details"), onclick:() => selectPane(key)}, label)));
  function selectPane(key) {
    for (const tab of tabs.querySelectorAll("[role=tab]")) tab.setAttribute("aria-selected", String(tab.id === `tab-${key}`));
    details.classList.toggle("inactive", key !== "details");
    chat.classList.toggle("inactive", key !== "chat");
  }
  selectPane(new URLSearchParams(location.search).get("pane") === "chat" ? "chat" : "details");
  view.replaceChildren(heading(item.title || "Untitled listing", `${sourceName(item.source)} · ${money(item)}`,
    button(selectedRun ? "Back to run listings" : "Back to listings", () => show("listings")),
    item.source === "fixture" ? null : link(item.url)),
    tabs, node("div", {className:"workspace"}, details, chat));
  details.querySelector('[name="state"]').addEventListener("change", (event) => action(async () => {
    await api(base + "/state", "PATCH", {state:event.target.value}); feedback.textContent = "Listing state saved.";
  }));
  view.querySelector("h1").setAttribute("tabindex","-1");
  view.querySelector("h1").focus();
}
const evidenceLabel = (kind) => kind === "seller_claim" ? "Seller claim" : "Your note";
const requestKey = () => (crypto.randomUUID ? crypto.randomUUID()
  : Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join(""));
const memoryGet = (key) => { try { return sessionStorage.getItem(key) ?? ""; } catch { return ""; } };
const memorySet = (key, value) => { try { value ? sessionStorage.setItem(key, value) : sessionStorage.removeItem(key); } catch { /* storage unavailable */ } };

async function evidencePanel(item) {
  const base = `/api/listings/${item.id}/evidence`;
  const list = node("div", {});
  async function refresh() {
    const data = await api(base);
    list.replaceChildren(...(data.items.length ? data.items.map((entry) => node("div", {className:"row"},
      node("div", {}, chip(evidenceLabel(entry.kind), entry.kind === "seller_claim"),
        node("p", {className:"prewrap"}, entry.content),
        node("small", {}, `${entry.origin} · ${entry.received_at ? "received " + timestamp(entry.received_at) + " · " : ""}added ${timestamp(entry.created)} · evidence:${entry.id}`))))
      : [empty("No seller updates or notes yet. The original listing is never overwritten.")]));
  }
  await refresh();
  const entry = form([
    select("kind", "Type", [["seller_claim","Seller reply or claim"],["user_note","My note"]], "seller_claim"),
    input("origin", "Where it came from", "seller via marketplace message", {maxlength:"160", required:true}),
    input("received_at", "Received at (local time, optional)", "", {type:"datetime-local"}),
    input("content", "Text", "", {multiline:true, maxlength:"4000", required:true})
  ], "Add update", async (data) => {
    const body = {kind:data.get("kind"), origin:data.get("origin"), content:data.get("content")};
    if (data.get("received_at")) body.received_at = new Date(data.get("received_at")).toISOString();
    await api(base, "POST", body);
    entry.elements.content.value = "";
    await refresh();
    document.dispatchEvent(new CustomEvent("evidence-changed", {detail:item.id}));
    feedback.textContent = "Update saved. The chat uses it on the next message.";
  });
  return node("section", {}, node("h2", {}, "Seller updates and notes"),
    node("p", {}, "Seller replies are stored as claims, not verified facts. Screenshots and files are not supported yet; transcribe them here."),
    node("details", {}, node("summary", {}, "Add a seller update or note"), entry), list);
}

async function chatPanel(item) {
  const pane = node("div", {id:"pane-chat", className:"pane chat", role:"tabpanel", "aria-labelledby":"tab-chat"});
  let [usage, conversations, evidence, projectList] = await Promise.all([api("/api/assistant/usage"),
    api(`/api/conversations?listing_id=${item.id}`), api(`/api/listings/${item.id}/evidence?limit=100`), api("/api/projects?status=active")]);
  const projectPicker = node("select", {"aria-label":"Project context"},
    node("option", {value:""}, "No project"), ...projectList.map((p) => node("option", {value:p.id}, p.name)));
  projectPicker.addEventListener("change", () => action(async () => {
    const projectId = projectPicker.value ? Number(projectPicker.value) : null;
    if (current) {
      current = await api(`/api/conversations/${current.id}`, "PATCH", {version:current.version, project_id:projectId});
      feedback.textContent = projectId ? "Project context changed. The next message uses only this project." : "Project context removed.";
    }
  }));
  let current = conversations[0] || null;
  let stream = null;
  let activeRun = null;
  const status = node("p", {role:"status", className:"muted"});
  const log = node("div", {className:"log", role:"log", "aria-live":"off", "aria-label":"Conversation", tabindex:"0"});
  const jump = button("Jump to latest", () => { log.scrollTop = log.scrollHeight; jump.hidden = true; });
  jump.hidden = true;
  const nearBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  log.addEventListener("scroll", () => { if (nearBottom()) jump.hidden = true; });
  const draftKey = () => `chat-draft:${item.id}:${current?.id ?? "new"}`;
  const compose = node("textarea", {name:"content", maxlength:"4000", rows:"3", "aria-label":"Message about this listing"});
  compose.addEventListener("input", () => memorySet(draftKey(), compose.value));
  const send = node("button", {className:"primary", type:"submit"}, "Send");
  const stop = button("Stop", () => action(async () => { if (activeRun) await api(`/api/assistant/runs/${activeRun}/cancel`, "POST"); }));
  stop.hidden = true;
  const picker = node("select", {"aria-label":"Conversation"});
  const contextBox = node("div", {});
  const header = node("div", {className:"actions"});

  function follow(apply) {
    const stick = nearBottom();
    apply();
    if (stick) log.scrollTop = log.scrollHeight; else jump.hidden = false;
  }
  function bubble(message) {
    const text = node("p", {className:"prewrap"}, message.content);
    const labels = {partial:"Partial reply · interrupted", cancelled:"Stopped by you", failed:"Failed"};
    return node("article", {className:`bubble ${message.role}`, "data-seq":message.seq},
      node("small", {}, message.role === "user" ? "You" : "Assistant", " · ", timestamp(message.created)),
      labels[message.status] ? chip(labels[message.status], true) : null, text,
      message.role === "assistant" ? node("div", {className:"actions"},
        button("Copy", () => action(async () => { await navigator.clipboard.writeText(message.content); feedback.textContent = "Reply copied."; })),
        message.status !== "complete" && message.run_id ? button("Retry", () => retry(message.run_id)) : null) : null);
  }
  async function loadMessages() {
    if (!current) { log.replaceChildren(empty("Ask anything about this listing. Its stored details and your seller updates are attached automatically.")); return; }
    const data = await api(`/api/conversations/${current.id}/messages?limit=100`);
    follow(() => log.replaceChildren(...(data.items.length ? data.items.map(bubble) : [empty("No messages yet.")])));
    const last = current.last_run;
    if (current.active_run) watch(current.active_run.id);
    else if (data.items.at(-1)?.role === "user" && last && ["failed", "cancelled", "interrupted"].includes(last.status)) {
      log.append(node("div", {className:"card"}, node("p", {className:"error"}, `No reply was saved (${last.error_code || last.status}).`),
        button("Retry", () => retry(last.id))));
    }
  }
  function setBusy(runId) {
    activeRun = runId;
    send.disabled = !!runId || !usage.enabled;
    stop.hidden = !runId;
  }
  function watch(runId) {
    if (stream) stream.close();
    setBusy(runId);
    const live = node("article", {className:"bubble assistant", "aria-busy":"true"}, node("small", {}, "Assistant · replying…"));
    const text = node("p", {className:"prewrap"});
    live.append(text);
    follow(() => log.append(live));
    status.textContent = "Assistant is replying…";
    stream = new EventSource(`/api/assistant/runs/${runId}/events`);
    stream.addEventListener("evidence", (event) => {
      const data = JSON.parse(event.data);
      live.append(node("small", {}, "Context: " + data.sources.map((source) => source.label).join(", ")
        + (data.omitted.length ? ` · omitted: ${data.omitted.length}` : "")));
    });
    stream.addEventListener("delta", (event) => {
      if (!pane.isConnected) { stream?.close(); stream = null; return; }
      follow(() => { text.textContent += JSON.parse(event.data).text; });
    });
    for (const kind of ["completed", "failed", "cancelled"]) {
      stream.addEventListener(kind, (event) => {
        const data = JSON.parse(event.data);
        stream.close(); stream = null; setBusy(null);
        status.textContent = kind === "completed" ? "Assistant reply complete." : kind === "cancelled" ? "Reply stopped." : `Reply failed: ${data.message}`;
        void refreshConversation();
      });
    }
    stream.onerror = () => {
      if (stream && stream.readyState === EventSource.CLOSED) {
        stream = null; setBusy(null); status.textContent = "Connection lost. Reloading saved messages.";
        void refreshConversation();
      }
    };
  }
  async function retry(runId) {
    await action(async () => { const result = await api(`/api/assistant/runs/${runId}/retry`, "POST", {request_key:requestKey()}); watch(result.run_id); });
  }
  async function refreshConversation() {
    if (!pane.isConnected) return;
    if (current) current = await api(`/api/conversations/${current.id}`);
    renderHeader(); renderContext(); await loadMessages();
  }
  function renderPicker(list) {
    picker.replaceChildren(...list.map((c) => node("option", {value:c.id}, c.title)), node("option", {value:""}, "+ New conversation"));
    picker.value = current ? String(current.id) : "";
  }
  picker.addEventListener("change", () => action(async () => {
    current = picker.value ? await api(`/api/conversations/${picker.value}`) : null;
    compose.value = memoryGet(draftKey());
    await refreshConversation();
  }));
  function renderHeader() {
    header.replaceChildren(
      current ? button("Rename", () => action(async () => {
        const title = prompt("Conversation name", current.title);
        if (!title?.trim()) return;
        current = await api(`/api/conversations/${current.id}`, "PATCH", {version:current.version, title:title.trim()});
        renderPicker(await api(`/api/conversations?listing_id=${item.id}`)); renderHeader();
      })) : null,
      current ? button("Archive", () => action(async () => {
        await api(`/api/conversations/${current.id}`, "PATCH", {version:current.version, archived:true});
        const rest = await api(`/api/conversations?listing_id=${item.id}`); current = rest[0] || null;
        renderPicker(rest); await refreshConversation(); feedback.textContent = "Conversation archived.";
      })) : null,
      current ? button("Delete", () => action(async () => {
        if (!confirm("Delete this conversation and its messages? Listing details, seller updates and notes are kept.")) return;
        await api(`/api/conversations/${current.id}`, "DELETE");
        const rest = await api(`/api/conversations?listing_id=${item.id}`); current = rest[0] || null;
        renderPicker(rest); await refreshConversation(); feedback.textContent = "Conversation deleted.";
      })) : null);
  }
  function renderContext() {
    projectPicker.value = current?.project_id ? String(current.project_id) : projectPicker.value;
    const excluded = new Set(current?.excluded ?? []);
    const options = [["listing:description", "Listing description", item.description != null],
      ...evidence.items.map((e) => [`evidence:${e.id}`, `${evidenceLabel(e.kind)} · ${e.content.slice(0, 60)}`, true]),
      ["project", "Selected project, its requirements, budget and allocated equipment", true],
      ["memory", "Your approved memory facts", true],
      ["history", "Earlier messages in this conversation", true]].filter(([, , present]) => present);
    contextBox.replaceChildren(
      node("p", {}, `Always attached: ${item.source === "fixture" ? "simulated " : ""}listing title, price, condition, location and timestamps from the latest stored revision.`),
      ...options.map(([ref, label]) => {
        const box = node("input", {type:"checkbox", name:ref, disabled:!current});
        box.checked = !excluded.has(ref);
        box.addEventListener("change", () => action(async () => {
          const next = [...contextBox.querySelectorAll("input[type=checkbox]")].filter((b) => !b.checked).map((b) => b.name);
          current = await api(`/api/conversations/${current.id}`, "PATCH", {version:current.version, excluded:next});
          feedback.textContent = "Context updated for the next message.";
        }));
        return node("label", {}, box, label);
      }),
      current ? null : node("small", {}, "Start the conversation to choose optional context."));
  }
  const composer = node("form", {className:"composer", onsubmit:(event) => {
    event.preventDefault();
    const content = compose.value.trim();
    if (!content || activeRun) return;
    send.disabled = true;
    void action(async () => {
      if (!current) {
        current = await api("/api/conversations", "POST", {listing_id:item.id, project_id:projectPicker.value ? Number(projectPicker.value) : null});
        renderPicker(await api(`/api/conversations?listing_id=${item.id}`));
        renderHeader(); renderContext();
        log.replaceChildren();
      }
      const key = requestKey();
      const result = await api(`/api/conversations/${current.id}/messages`, "POST", {content, request_key:key, listing_id:item.id, project_id:current.project_id ?? null});
      memorySet(draftKey(), "");
      compose.value = "";
      log.querySelector(":scope > .card")?.remove();
      follow(() => log.append(bubble({role:"user", content, created:Date.now() / 1000, status:"complete"})));
      watch(result.run_id);
    }).finally(() => { if (!activeRun) send.disabled = !usage.enabled; });
  }}, compose, node("div", {className:"actions"}, send, stop, jump));
  compose.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) composer.requestSubmit();
  });
  const onEvidence = (event) => {
    if (!pane.isConnected) { document.removeEventListener("evidence-changed", onEvidence); return; }
    if (event.detail !== item.id) return;
    void action(async () => { evidence = await api(`/api/listings/${item.id}/evidence?limit=100`); renderContext(); });
  };
  document.addEventListener("evidence-changed", onEvidence);
  renderPicker(conversations); renderHeader(); renderContext();
  compose.value = memoryGet(draftKey());
  send.disabled = !usage.enabled;
  pane.append(node("h2", {}, "Chat about this listing"),
    usage.enabled ? node("p", {}, chip(usage.provider.label), usage.provider.cloud ? chip("Cloud: data leaves this computer", true) : chip("Runs locally"))
      : node("p", {className:"error"}, "The assistant is disabled. Listing details, notes and seller drafts still work."),
    node("div", {className:"actions"}, node("label", {}, "Conversation", picker), node("label", {}, "Project context", projectPicker), header),
    node("details", {className:"context"}, node("summary", {}, "Context sent with each message"), contextBox),
    log, status, composer,
    node("small", {}, "The assistant cannot contact sellers, make offers or change records. Ctrl+Enter sends."));
  await loadMessages();
  return pane;
}
const amount = (value, currency) => value == null ? "Not set" : `${value} ${currency}`;
const optionalText = (data, key) => data.get(key)?.trim() || null;
const projectStatuses = ["active", "paused", "completed", "archived"];
const roles = ["candidate", "shortlisted", "rejected", "purchased"];

function budgetCard(summary) {
  return node("div", {className:"grid"},
    ...[["Budget", amount(summary.budget, summary.currency)], ["Confirmed purchases", amount(summary.actual, summary.currency)],
      ["Committed offers", summary.committed == null ? "Not tracked yet" : amount(summary.committed, summary.currency)],
      ["Remaining", amount(summary.remaining, summary.currency)]]
      .map(([label, value]) => node("div", {className:"card stat"}, node("small", {}, label), node("strong", {}, value))),
    summary.unconverted.length ? node("p", {}, "Not converted: " + summary.unconverted.map((u) => `${u.amount} ${u.currency}`).join(", ")) : null);
}
async function projects() {
  const items = await api("/api/projects");
  return [heading("Projects", "Purchasing goals with requirements, budgets and owned equipment.", button("Refresh", () => show("projects"))),
    node("details", {open:!items.length}, node("summary", {}, "Create a project"), form([
      input("name", "Name", "", {required:true, maxlength:"160"}), input("description", "Goal", "", {multiline:true, maxlength:"4000"}),
      input("budget", "Budget (optional)", "", {type:"number", min:"0", step:".01"}), input("currency", "Currency", "CAD", {required:true}),
      input("target_date", "Target date (optional)", "", {type:"date"})
    ], "Create project", async (data) => {
      const created = await api("/api/projects", "POST", {name:data.get("name"), description:data.get("description"),
        budget:optionalText(data, "budget"), currency:data.get("currency").trim().toUpperCase(), target_date:optionalText(data, "target_date")});
      await projectDetail(created.id); feedback.textContent = "Project created.";
    })),
    ...(items.length ? items.map((p) => node("article", {className:"row"}, node("div", {},
      node("h3", {}, p.name), chip(p.status, p.status !== "active"),
      node("p", {}, `Budget ${amount(p.budget_summary.budget, p.currency)} · remaining ${amount(p.budget_summary.remaining, p.currency)}`)),
      button("Open project", () => action(() => projectDetail(p.id))))) : [empty("No projects yet. Examples: Home Media Server, Local AI Server.")])];
}
function requirementRow(r = {}) {
  const row = node("fieldset", {className:"requirement"}, node("legend", {}, "Requirement"),
    input("label", "Label", r.label ?? "", {required:true}), input("attribute", "Attribute", r.attribute ?? "", {required:true}),
    select("operator", "Operator", [">=","<=","=","!=","contains","present"].map((o) => [o, o]), r.operator ?? ">="),
    input("value", "Value", r.value ?? ""), input("unit", "Unit", r.unit ?? ""),
    select("hardness", "Type", [["hard","Hard (must pass)"],["soft","Preference (weighted)"]], r.hardness ?? "hard"),
    input("weight", "Weight (preferences only)", r.weight ?? 0, {type:"number", min:"0", max:"100"}),
    input("evidence_required", "Needs evidence", r.evidence_required, {type:"checkbox"}));
  row.append(button("Remove requirement", () => row.remove()));
  return row;
}
async function projectDetail(id) {
  const renderId = ++generation;
  const [p, inventory, allListings] = await Promise.all([api(`/api/projects/${id}`), api("/api/inventory"), api("/api/listings?limit=100")]);
  if (renderId !== generation) return;
  const reload = async (message) => { await projectDetail(id); if (message) feedback.textContent = message; };
  const edit = form([
    input("name", "Name", p.name, {required:true, maxlength:"160"}), input("description", "Goal", p.description, {multiline:true}),
    select("status", "Status", projectStatuses.map((s) => [s, s]), p.status), input("target_date", "Target date", p.target_date ?? "", {type:"date"}),
    p.budget == null ? input("budget", "Budget (first time only)", "", {type:"number", min:"0", step:".01"}) : null,
    input("notes", "Notes", p.notes, {multiline:true})
  ], "Save project", async (data) => {
    const body = {version:p.version, name:data.get("name"), description:data.get("description"), status:data.get("status"),
      target_date:optionalText(data, "target_date"), notes:data.get("notes")};
    if (p.budget == null && optionalText(data, "budget")) body.budget = optionalText(data, "budget");
    await api(`/api/projects/${id}`, "PATCH", body); await reload("Project saved.");
  });
  const budget = p.budget == null ? null : node("details", {}, node("summary", {}, "Change budget (reason required)"), form([
    input("budget", "New budget (empty removes it)", p.budget, {type:"number", min:"0", step:".01"}),
    input("reason", "Reason", "", {required:true, minlength:"3", maxlength:"500"})
  ], "Change budget", async (data) => {
    await api(`/api/projects/${id}/budget`, "POST", {version:p.version, budget:optionalText(data, "budget"), reason:data.get("reason")});
    await reload("Budget changed and recorded.");
  }));
  const rows = node("div", {}, ...p.requirements.map(requirementRow));
  const requirementsForm = node("form", {onsubmit:(event) => {
    event.preventDefault();
    void action(async () => {
      const requirements = [...rows.querySelectorAll("fieldset")].map((set) => {
        const value = (name) => set.querySelector(`[name=${name}]`);
        return {label:value("label").value, attribute:value("attribute").value, operator:value("operator").value,
          value:value("value").value.trim() || null, unit:value("unit").value.trim() || null, hardness:value("hardness").value,
          weight:value("hardness").value === "soft" ? Number(value("weight").value) : 0, evidence_required:value("evidence_required").checked};
      });
      await api(`/api/projects/${id}/requirements`, "PUT", {version:p.version, requirements}); await reload("Requirements saved.");
    });
  }}, rows, node("div", {className:"actions"}, button("Add requirement", () => rows.append(requirementRow())),
    node("button", {className:"primary"}, "Save requirements")));
  const linkable = allListings.items.filter((l) => !p.listings.some((x) => x.listing_id === l.id));
  view.replaceChildren(heading(p.name, `${p.status} project${p.target_date ? " · target " + p.target_date : ""}`,
      button("Back to projects", () => show("projects"))),
    p.description ? node("p", {className:"prewrap"}, p.description) : null,
    budgetCard(p.budget_summary), budget,
    node("details", {}, node("summary", {}, "Edit project"), edit),
    section("Requirements", [node("p", {}, "Hard requirements are pass/fail and cannot be outweighed by price. Preferences carry weights up to 100 in total."), requirementsForm], ""),
    section("Listings", [
      ...p.listings.map((l) => node("article", {className:"row"}, node("div", {}, node("h3", {}, l.title || "Untitled listing"),
        node("p", {}, l.price == null ? "Price unknown" : `${l.price} ${l.currency || ""}`), l.source === "fixture" ? chip("Simulated") : null),
        node("div", {className:"actions"}, (() => {
          const control = select("role", "Role", roles.map((r) => [r, r]), l.role);
          control.querySelector("select").addEventListener("change", (event) => action(async () => {
            await api(`/api/projects/${id}/listings/${l.listing_id}`, "PATCH", {role:event.target.value}); feedback.textContent = "Role saved.";
          }));
          return control;
        })(), button("Open listing", () => action(() => detail(l.listing_id))),
        button("Unlink", () => action(async () => { await api(`/api/projects/${id}/listings/${l.listing_id}`, "DELETE"); await reload("Listing unlinked."); }))))),
      linkable.length ? form([select("listing_id", "Add a stored listing", linkable.map((l) => [l.id, `${l.title || "Untitled"} · ${money(l)}`]), linkable[0].id),
        select("role", "Role", roles.slice(0, 3).map((r) => [r, r]), "candidate")], "Link listing", async (data) => {
        await api(`/api/projects/${id}/listings`, "POST", {listing_id:Number(data.get("listing_id")), role:data.get("role")}); await reload("Listing linked.");
      }) : null], "No listings linked."),
    section("Owned equipment for this project", [
      ...p.allocations.map((a) => node("div", {className:"row"}, node("div", {}, node("strong", {}, `${a.allocated} × ${a.name}`),
        node("p", {}, [a.manufacturer, a.model].filter(Boolean).join(" ") || a.category), a.override_reason ? chip(`Override: ${a.override_reason}`, true) : null),
        button("Release", () => action(async () => { await api(`/api/projects/${id}/allocations/${a.id}`, "DELETE"); await reload("Allocation released."); })))),
      inventory.length ? form([select("item", "Item", inventory.map((i) => [i.id, `${i.name} (${i.quantity - i.allocated} of ${i.quantity} unallocated)`]), inventory[0].id),
        input("quantity", "Quantity", 1, {type:"number", min:"1", required:true}), input("override_reason", "Override reason (only to exceed available)")],
      "Allocate", async (data) => {
        const body = {quantity:Number(data.get("quantity"))};
        if (optionalText(data, "override_reason")) body.override_reason = optionalText(data, "override_reason");
        await api(`/api/projects/${id}/allocations/${data.get("item")}`, "PUT", body); await reload("Allocated.");
      }) : empty("Add owned equipment under Inventory to allocate it.")], ""),
    section("Purchases", [...p.purchases.map((x) => node("div", {className:"row"}, node("strong", {}, `${x.amount} ${x.currency}`), x.description, timestamp(x.purchased_at))),
      node("details", {}, node("summary", {}, "Record a completed purchase"), form([
        input("description", "What you bought", "", {required:true}), input("amount", "Amount paid", "", {type:"number", min:"0", step:".01", required:true}),
        input("currency", "Currency", p.currency, {required:true}), input("purchased_at", "When", "", {type:"datetime-local", required:true}),
        select("listing_id", "From listing (optional)", [["", "None"], ...p.listings.map((l) => [l.listing_id, l.title || "Untitled"])], ""),
        input("confirm", "I confirm I completed this purchase myself", false, {type:"checkbox", required:true})
      ], "Record purchase", async (data) => {
        await api("/api/purchases", "POST", {confirm:data.has("confirm"), description:data.get("description"), amount:data.get("amount"),
          currency:data.get("currency").trim().toUpperCase(), purchased_at:new Date(data.get("purchased_at")).toISOString(), project_id:id,
          listing_id:data.get("listing_id") ? Number(data.get("listing_id")) : null});
        await reload("Purchase recorded. Add it to Inventory yourself if you want to track it as owned.");
      }))], ""),
    p.budget_changes.length ? section("Budget history", p.budget_changes.map((c) => node("p", {}, `${timestamp(c.created)} · ${c.previous ?? "none"} → ${c.new ?? "none"} · ${c.reason}`)), "") : null);
  view.querySelector("h1").setAttribute("tabindex", "-1");
  view.querySelector("h1").focus();
}
function inventoryForm(item = {}) {
  const attributes = Object.entries(item.attributes ?? {}).map(([k, v]) => `${k}=${v}`).join("\n");
  return form([
    input("name", "Name", item.name ?? "", {required:true}), input("category", "Category", item.category ?? "", {required:true}),
    input("manufacturer", "Manufacturer", item.manufacturer ?? ""), input("model", "Exact model", item.model ?? ""),
    input("quantity", "Quantity", item.quantity ?? 1, {type:"number", min:"0", required:true}), input("condition", "Condition", item.condition ?? ""),
    select("lifecycle", "Lifecycle", [["in_use","In use"],["spare","Spare"],["retired","Retired"],["sold","Sold"]], item.lifecycle ?? "in_use"),
    input("acquisition_cost", "Cost (optional)", item.acquisition_cost ?? "", {type:"number", min:"0", step:".01"}),
    input("acquisition_currency", "Cost currency", item.acquisition_currency ?? ""),
    item.id ? null : input("serial", "Serial number (private, optional)"),
    input("attributes", "Specifications (one key=value per line)", attributes, {multiline:true}), input("notes", "Notes", item.notes ?? "", {multiline:true})
  ], item.id ? "Save item" : "Add item", async (data) => {
    const specs = {};
    for (const line of data.get("attributes").split("\n").map((l) => l.trim()).filter(Boolean)) {
      const [key, ...rest] = line.split("=");
      if (!rest.length) throw new Error(`Specification line needs key=value: ${line}`);
      specs[key.trim()] = rest.join("=").trim();
    }
    const body = {name:data.get("name"), category:data.get("category"), manufacturer:optionalText(data, "manufacturer"),
      model:optionalText(data, "model"), quantity:Number(data.get("quantity")), condition:optionalText(data, "condition"),
      lifecycle:data.get("lifecycle"), acquisition_cost:optionalText(data, "acquisition_cost"),
      acquisition_currency:optionalText(data, "acquisition_currency")?.toUpperCase() ?? null, attributes:specs, notes:data.get("notes")};
    if (!item.id && optionalText(data, "serial")) body.serial = optionalText(data, "serial");
    if (item.id) await api(`/api/inventory/${item.id}`, "PATCH", {...body, version:item.version});
    else await api("/api/inventory", "POST", body);
    await show("inventory"); feedback.textContent = "Inventory saved.";
  });
}
async function inventoryView() {
  const items = await api("/api/inventory?include_retired=true");
  return [heading("Inventory", "Equipment you own. Serial numbers stay private and are never sent to the assistant.", button("Refresh", () => show("inventory"))),
    node("details", {open:!items.length}, node("summary", {}, "Add owned equipment"), inventoryForm()),
    ...(items.length ? items.map((i) => node("article", {className:"card"}, node("h3", {}, i.name),
      chip(i.category), chip(i.lifecycle), chip(`${i.allocated} of ${i.quantity} allocated`, i.allocated > i.quantity),
      node("p", {}, [i.manufacturer, i.model].filter(Boolean).join(" ") || "Model unknown"),
      Object.keys(i.attributes).length ? node("p", {}, Object.entries(i.attributes).map(([k, v]) => `${k}: ${v}`).join(" · ")) : null,
      node("details", {}, node("summary", {}, "Edit item"), inventoryForm(i)),
      button("Delete", () => action(async () => {
        if (!confirm(`Delete “${i.name}” from inventory?`)) return;
        await api(`/api/inventory/${i.id}`, "DELETE"); await show("inventory");
      })))) : [empty("No owned equipment recorded. Inventory is never assumed to be complete.")])];
}
async function memoryView() {
  const [facts, projectList] = await Promise.all([api("/api/memory"), api("/api/projects")]);
  const projectName = (id) => projectList.find((p) => p.id === id)?.name ?? `project ${id}`;
  const factRow = (f) => node("article", {className:"row"}, node("div", {},
      node("strong", {}, `${f.key}: ${f.value}${f.unit ? " " + f.unit : ""}`), " ", chip(f.status, f.status === "suggested"),
      node("p", {}, `${f.scope}${f.project_id ? " · " + projectName(f.project_id) : ""}${f.listing_id ? " · listing " + f.listing_id : ""} · ${f.source} · ${f.origin}`)),
    node("div", {className:"actions"},
      f.status === "suggested" ? button("Approve", () => patch(f, {action:"approve"}, "Fact approved.")) : null,
      ["suggested","approved"].includes(f.status) ? button("Correct", () => {
        const value = prompt(`Correct value for ${f.key}`, f.value);
        if (value?.trim()) void patch(f, {action:"correct", value:value.trim()}, "Correction saved; the old value is superseded.");
      }) : null,
      ["suggested","approved"].includes(f.status) ? button("Reject", () => patch(f, {action:"reject"}, "Fact rejected.")) : null,
      button("Delete", () => action(async () => {
        if (!confirm(`Delete fact “${f.key}”?`)) return;
        await api(`/api/memory/${f.id}`, "DELETE"); await show("memory"); feedback.textContent = "Fact deleted.";
      }))));
  const patch = (f, body, message) => action(async () => { await api(`/api/memory/${f.id}`, "PATCH", {version:f.version, ...body}); await show("memory"); feedback.textContent = message; });
  const active = facts.filter((f) => ["suggested","approved"].includes(f.status));
  return [heading("Memory", "Only facts you approve are used in chat. Deleting a conversation does not delete approved facts.",
      button("Refresh", () => show("memory")), node("a", {className:"button", href:"/api/memory/export", download:"memory-export.json"}, "Export approved facts")),
    node("details", {}, node("summary", {}, "Add a fact"), form([
      select("scope", "Applies to", [["global","Everything"], ...projectList.map((p) => [`project:${p.id}`, `Project · ${p.name}`])], "global"),
      input("key", "Fact", "", {required:true, maxlength:"80"}), input("value", "Value", "", {required:true, maxlength:"500"}), input("unit", "Unit")
    ], "Save fact", async (data) => {
      const scope = data.get("scope");
      await api("/api/memory", "POST", {scope:scope === "global" ? "global" : "project", project_id:scope === "global" ? null : Number(scope.split(":")[1]),
        key:data.get("key"), value:data.get("value"), unit:optionalText(data, "unit")});
      await show("memory"); feedback.textContent = "Fact saved and approved.";
    })),
    section("Active facts", active.map(factRow), "No approved or suggested facts."),
    node("details", {}, node("summary", {}, "Rejected and superseded history"), ...facts.filter((f) => !active.includes(f)).map(factRow))];
}
async function listingProjects(item) {
  const [linked, all] = await Promise.all([api(`/api/listings/${item.id}/projects`), api("/api/projects?status=active")]);
  const available = all.filter((p) => !linked.some((l) => l.id === p.id));
  return node("section", {}, node("h2", {}, "Projects"),
    ...(linked.length ? linked.map((p) => node("div", {className:"row"}, node("span", {}, `${p.name} · ${p.role}`),
      button("Open project", () => action(() => projectDetail(p.id))))) : [empty("Not linked to a project. Listings don't have to belong to one.")]),
    available.length ? form([select("project", "Add to project", available.map((p) => [p.id, p.name]), available[0].id),
      select("role", "Role", roles.slice(0, 3).map((r) => [r, r]), "candidate")], "Link to project", async (data) => {
      await api(`/api/projects/${data.get("project")}/listings`, "POST", {listing_id:item.id, role:data.get("role")});
      await detail(item.id); feedback.textContent = "Linked to project.";
    }) : null);
}
async function comparisonsView() {
  const [items, stored, projectList] = await Promise.all([api("/api/comparisons"), api("/api/listings?limit=100"), api("/api/projects?status=active")]);
  const picker = node("fieldset", {className:"checklist"}, node("legend", {}, "Listings (choose 2 to 5)"),
    ...stored.items.map((l) => node("label", {}, node("input", {type:"checkbox", name:"listing", value:l.id}), `${l.title || "Untitled"} · ${money(l)}${l.source === "fixture" ? " · simulated" : ""}`)));
  return [heading("Comparisons", "Side-by-side checks of stored listings against a project. Deterministic, never a purchase decision.", button("Refresh", () => show("comparisons"))),
    node("details", {open:!items.length}, node("summary", {}, "New comparison"), stored.items.length < 2 ? empty("Store at least two listings first.") : form([
      input("title", "Title (optional)"), select("project", "Project", [["", "No project"], ...projectList.map((p) => [p.id, p.name])], ""), picker
    ], "Compare", async (data) => {
      const ids = data.getAll("listing").map(Number);
      if (ids.length < 2 || ids.length > 5) throw new Error("Choose between 2 and 5 listings.");
      const created = await api("/api/comparisons", "POST", {title:optionalText(data, "title"), listing_ids:ids,
        project_id:data.get("project") ? Number(data.get("project")) : null});
      await comparisonDetail(created.id);
    })),
    ...(items.length ? items.map((c) => node("article", {className:"row"}, node("div", {}, node("h3", {}, c.title), node("p", {}, `${c.listing_ids.length} listings · updated ${timestamp(c.updated)}`)),
      button("Open comparison", () => action(() => comparisonDetail(c.id))))) : [empty("No comparisons yet.")]),
    calculatorPanel()];
}
async function comparisonDetail(id, snapshotId = null) {
  const renderId = ++generation;
  const c = await api(`/api/comparisons/${id}${snapshotId ? "?snapshot_id=" + snapshotId : ""}`);
  if (renderId !== generation) return;
  const snap = c.snapshot;
  const rows = [
    ["Price", (l) => l.price == null ? "Unknown" : `${l.price} ${l.currency || "(currency unknown)"}`],
    ["Total cost basis", (l) => l.total_cost.basis],
    ["Budget share", (l) => l.budget_share_percent == null ? "—" : `${l.budget_share_percent}% of remaining`],
    ["Condition", (l) => l.condition || "Unknown"],
    ["Drives included", (l) => l.drives.stated ? `${l.drives.count} × ${l.drives.size_tb} TB (stated: “${l.drives.matched}”)` : l.drives.note],
    ["Usable capacity (calculated)", (l) => l.capacities.length ? l.capacities.map((x) => `${x.layout}: ${x.usable_tb} TB / ${x.usable_tib} TiB`).join("\n") : "Unknown"],
    ...(snap.project ? [["Requirements", (l) => l.requirements.map((r) => `${r.result === "meets" ? "✓" : r.result === "fails" ? "✗" : "?"} ${r.label} (${r.hardness}): ${r.result} — ${r.basis}`).join("\n") || "None"],
      ["Outcome", (l) => l.outcome]] : []),
    ["Seller claims", (l) => l.seller_claims.map((s) => `“${s.content}” [${s.ref}]`).join("\n") || "None recorded"],
    ["Unknown fields", (l) => l.unknown.join(", ") || "None"],
    ["Revision", (l) => `${l.ref}${l.simulated ? " · simulated" : ""}`],
  ];
  const live = snap.listings.filter((l) => !l.missing);
  const table = node("div", {className:"table-scroll", tabindex:"0", role:"region", "aria-label":"Comparison table"},
    node("table", {}, node("caption", {}, `Snapshot ${timestamp(snap.created)} · ${snap.algorithm_version}${snap.project ? " · project " + snap.project.name : ""}`),
      node("thead", {}, node("tr", {}, node("th", {scope:"col"}, "Attribute"), ...live.map((l) => node("th", {scope:"col"},
        node("button", {type:"button", className:"linkish", onclick:() => action(() => detail(l.listing_id))}, l.title || "Untitled listing"))))),
      node("tbody", {}, ...rows.map(([label, cell]) => node("tr", {}, node("th", {scope:"row"}, label), ...live.map((l) => node("td", {className:"prewrap"}, cell(l))))))));
  view.replaceChildren(heading(c.title, snap.project ? `For ${snap.project.name}` : "No project selected", button("Back to comparisons", () => show("comparisons")),
      button("Recompute now", () => action(async () => { await api(`/api/comparisons/${id}/refresh`, "POST"); await comparisonDetail(id); feedback.textContent = "New snapshot saved."; }), c.stale ? "primary" : "")),
    c.stale && !snapshotId ? node("div", {className:"card", role:"alert"}, node("strong", {}, "This snapshot is stale. "), c.stale_reasons.join(". ") + ".") : null,
    snapshotId ? node("p", {}, chip("Viewing an earlier snapshot", true)) : null,
    table, ...snap.notes.map((n) => node("p", {}, n)),
    snap.listings.some((l) => l.missing) ? node("p", {className:"error"}, "Some listings in this comparison no longer exist.") : null,
    c.history.length > 1 ? section("Snapshot history", c.history.map((h) => node("div", {className:"row"}, node("span", {}, `${timestamp(h.created)} · ${h.algorithm_version}`),
      button("View", () => action(() => comparisonDetail(id, h.id))))), "") : null);
  view.querySelector("h1").setAttribute("tabindex", "-1");
  view.querySelector("h1").focus();
}
function calculatorPanel() {
  const out = node("pre", {className:"card", role:"status"}, "Results appear here.");
  const render = (result) => { out.textContent = Object.entries(result).map(([k, v]) => `${k}: ${Array.isArray(v) ? "\n  - " + v.join("\n  - ") : v ?? "—"}`).join("\n"); };
  return node("section", {}, node("h2", {}, "Calculator"),
    node("p", {}, "Exact arithmetic with stated assumptions. Use it instead of estimating capacities or running costs."),
    form([input("drives", "Drive sizes in TB (comma-separated)", "4, 4, 4, 4", {required:true}),
      select("layout", "Layout", [["raid6","RAID6 / RAID-Z2"],["raid5","RAID5 / RAID-Z1"],["mirror","Mirror"],["raid10","RAID10"],["stripe","Stripe (RAID0)"],["jbod","JBOD"]], "raid6")],
    "Calculate storage", async (data) => render(await api("/api/calculations/storage", "POST", {layout:data.get("layout"),
      drives_tb:data.get("drives").split(",").map((v) => v.trim()).filter(Boolean)}))),
    form([input("watts", "Average watts", "", {type:"number", min:"0", step:".1", required:true}), input("hours", "Hours per day", 24, {type:"number", min:"0.1", max:"24", step:".1"}),
      input("rate", "Rate per kWh (optional)", "", {type:"number", min:"0", step:".0001"}), input("currency", "Rate currency", "CAD")],
    "Calculate energy", async (data) => {
      const body = {watts:data.get("watts"), hours_per_day:data.get("hours")};
      if (optionalText(data, "rate")) Object.assign(body, {rate_per_kwh:optionalText(data, "rate"), currency:data.get("currency").trim().toUpperCase()});
      render(await api("/api/calculations/energy", "POST", body));
    }), out);
}
async function activity() {
  const runs = await api("/api/runs");
  return [heading("Run activity", "Open a completed run to see every distinct listing it returned.", button("Refresh", () => show("activity"))),
    ...(runs.length ? runs.map((run) => node("article", {className:"row"}, node("div", {},
      node("h3", {}, run.name), node("p", {}, `${sourceName(run.source)} · ${timestamp(run.started)}`),
      run.result_count != null ? node("p", {}, `${run.result_count} ${run.result_count === 1 ? "listing" : "listings"} returned`)
        : run.status === "success" ? node("p", {}, "Listing results were not recorded for this older run.") : null,
      run.error ? node("p", {className:"error"}, run.error) : null),
    node("div", {className:"actions"}, chip(run.status, run.status !== "success"),
      run.result_count != null ? button("View listings", () => openListings(run)) : null))) : [empty("No runs have been recorded.")])];
}
async function settings() {
  const {settings:config, delivery, provider} = await api("/api/notifications");
  const destination = provider === "telegram" ? "Telegram sends alerts only to your configured chat." : "Console alerts appear in the application logs.";
  return [heading("Notifications", destination, button("Refresh", () => show("settings")),
    button("Send test notification", () => action(async () => {
      await api("/api/notifications/test", "POST"); await show("settings"); feedback.textContent = "Test notification queued.";
    }), "primary")),
    form([input("muted","Mute alerts",config.muted,{type:"checkbox"}),
      input("minimum_score","Minimum score",config.minimum_score,{type:"number",min:"0",max:"100",required:true}),
      input("minimum_priority","Minimum watchlist priority",config.minimum_priority,{type:"number",min:"0",max:"10",required:true})],
    "Save settings", async (data) => {
      await api("/api/notifications/settings","PUT",{muted:data.has("muted"),minimum_score:Number(data.get("minimum_score")),minimum_priority:Number(data.get("minimum_priority"))});
      feedback.textContent = "Notification settings saved.";
    }),
    section("Delivery history", delivery.map((item) => node("details", {className:"card"},
      node("summary", {}, `${item.status} · ${item.event_key} · ${item.attempts} attempts`),
      item.error ? node("p", {className:"error"}, item.error) : null,
      node("pre", {}, item.payload || "No retained payload."))), "No notifications have been recorded.")];
}
const views = {overview, listings, watchlists, projects, inventory:inventoryView, comparisons:comparisonsView, memory:memoryView, activity, settings};
async function show(name) {
  const renderId = ++generation;
  feedback.textContent = "";
  for (const tab of document.querySelectorAll("[data-view]")) {
    if (tab.dataset.view === name) tab.setAttribute("aria-current","page");
    else tab.removeAttribute("aria-current");
  }
  view.replaceChildren(empty("Loading…"));
  try {
    const content = await views[name]();
    if (renderId === generation) view.replaceChildren(...content);
  } catch (error) {
    if (renderId === generation) view.replaceChildren(node("div", {className:"card"}, node("p", {className:"error"}, error.message), button("Retry", () => show(name))));
  }
}
for (const tab of document.querySelectorAll("[data-view]")) {
  tab.addEventListener("click", () => tab.dataset.view === "listings" ? openListings() : show(tab.dataset.view));
}
const linkedListing = new URLSearchParams(location.search).get("listing");
if (linkedListing && /^[1-9][0-9]*$/.test(linkedListing)) {
  const listingTab = document.querySelector('[data-view="listings"]');
  listingTab.setAttribute("aria-current", "page");
  view.replaceChildren(empty("Loading…"));
  detail(linkedListing).catch((error) => {
    view.replaceChildren(node("div", {className:"card"}, node("p", {className:"error"}, error.message),
      button("Back to listings", () => show("listings"))));
  });
} else {
  void show("overview");
}
