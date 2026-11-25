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
  const metrics = [["Enabled watchlists", summary.enabled], ["Confirmed listings", summary.confirmed],
    ["Last successful poll", timestamp(summary.last_sync)], ["Failed runs retained", summary.failed]];
  return [heading("Deal overview", "Track offers across your watchlists and review new matches.",
    button("Refresh", () => show("overview")), button("Add demo watchlists", () => action(async () => {
      const result = await api("/api/demo", "POST");
      await show("watchlists");
      feedback.textContent = result.length ? "Demo watchlists added. Fixture data is simulated." : "Demo watchlists already exist.";
    }), "primary")),
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
          const result = await api(`/api/watchlists/${item.id}/run`, "POST"); feedback.textContent = `Run ${result.status}. See Activity for results.`;
        })) : null,
        button("Delete", () => action(async () => {
          if (!confirm(`Delete watchlist “${item.name}”?`)) return;
          await api(`/api/watchlists/${item.id}`, "DELETE"); await show("watchlists");
        })))))];
}
const feedFilters = {source:"", status:"", state:"", sort:"recent", watchlist_id:"", offset:0};

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
    await detail(result.listing_ids[0]);
    feedback.textContent = "Listing imported.";
  });
}
async function listings() {
  const query = new URLSearchParams(Object.entries(feedFilters).filter(([, value]) => value !== ""));
  const [data, watches] = await Promise.all([api("/api/listings?" + query), api("/api/watchlists")]);
  const filters = node("div", {className:"filters", onchange:(event) => {
    feedFilters[event.target.name] = event.target.value; feedFilters.offset = 0; void show("listings");
  }},
    select("source","Source",[["","All"],...["fixture","manual","alert"].map((s) => [s,sourceName(s)])],feedFilters.source),
    select("status","Match",[["","All"],["confirmed","Confirmed"],["uncertain","Uncertain"],["unmatched","Unmatched"]],feedFilters.status),
    select("state","State",[["","All"],...states.map((s) => [s,s])],feedFilters.state),
    select("watchlist_id","Watchlist",[["","All"],...watches.map((watch) => [watch.id,watch.name])],feedFilters.watchlist_id),
    select("sort","Sort",[["recent","Recent"],["price","Lowest price"],["score","Highest score"]],feedFilters.sort));
  return [heading("Listings", "Review match reasons, price revisions, and seller message drafts.", button("Refresh", () => show("listings"))),
    node("details", {}, node("summary", {}, "Import listing manually"), importForm()), filters,
    ...(data.items.length ? data.items.map((item) => node("article", {className:"row"},
      node("div", {}, node("h3", {}, item.title || "Untitled listing"), node("p", {}, `${money(item)} · ${item.location || "Location unknown"}`),
        chip(sourceName(item.source)), chip(item.state),
        item.matches.map((m) => chip(`${m.watchlist} · ${m.status} · ${m.score}`, m.status === "uncertain"))),
      button("View details", () => action(() => detail(item.id))))) : [empty("No listings match these filters.")]),
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
  view.replaceChildren(heading(item.title || "Untitled listing", `${sourceName(item.source)} · ${money(item)}`,
    button("Back to listings", () => show("listings")), link(item.url)),
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
    node("h2", {}, "Seller message"), draftForm);
  view.querySelector('[name="state"]').addEventListener("change", (event) => action(async () => {
    await api(base + "/state", "PATCH", {state:event.target.value}); feedback.textContent = "Listing state saved.";
  }));
  view.querySelector("h1").setAttribute("tabindex","-1");
  view.querySelector("h1").focus();
}
async function activity() {
  const runs = await api("/api/runs");
  return [heading("Run activity", "Review poll results and provider failures.", button("Refresh", () => show("activity"))),
    ...(runs.length ? runs.map((run) => node("article", {className:"row"}, node("div", {},
      node("h3", {}, run.name), node("p", {}, `${sourceName(run.source)} · ${timestamp(run.started)}`),
      run.error ? node("p", {className:"error"}, run.error) : null), chip(run.status, run.status !== "success"))) : [empty("No runs have been recorded.")])];
}
async function settings() {
  const {settings:config, delivery, provider} = await api("/api/notifications");
  return [heading("Notifications", `Delivery provider · ${provider}`, button("Refresh", () => show("settings")),
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
const views = {overview, listings, watchlists, activity, settings};
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
for (const tab of document.querySelectorAll("[data-view]")) tab.addEventListener("click", () => show(tab.dataset.view));
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
