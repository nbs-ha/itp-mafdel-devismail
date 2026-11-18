"use strict";
/* Panneau Outlook "Devis IA". JS simple, sans dépendance ; données insérées en textContent (pas d'innerHTML). */

const $ = (id) => document.getElementById(id);
const state = { sid: null, p: null, busy: false };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

/* ---------- jeton (POC) ---------- */
function getToken() { try { return localStorage.getItem("devisia_token") || ""; } catch { return ""; } }
function setToken(v) { try { localStorage.setItem("devisia_token", v); } catch { /* ignoré */ } }

function statut(msg, erreur) { const s = $("statut"); s.textContent = msg || ""; s.className = erreur ? "erreur" : ""; }

function setBusy(b) {
  state.busy = b;
  for (const id of ["btn-analyse", "btn-envoyer", "btn-creer"]) $(id).disabled = b;
}

async function api(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": getToken() },
    body: JSON.stringify(body),
  });
  let data = null;
  try { data = await r.json(); } catch { /* corps vide */ }
  if (!r.ok) {
    let detail = data && data.detail;
    if (detail && detail.bloquants) detail = "Création impossible : " + detail.bloquants.join(" ; ");
    if (r.status === 401) detail = "Jeton d'accès invalide (voir Réglages).";
    const e = new Error(typeof detail === "string" ? detail : "Erreur " + r.status);
    e.status = r.status;
    throw e;
  }
  return data;
}

/* ---------- lecture du mail (Outlook ou échantillon de dev) ---------- */
function outlookDisponible() {
  return typeof Office !== "undefined" && !window.__noOffice && Office.context && Office.context.mailbox && Office.context.mailbox.item;
}

const asyncOffice = (fn) => new Promise((res, rej) => fn((r) => (r.status === "succeeded" ? res(r.value) : rej(new Error(r.error ? r.error.message : "Erreur Office")))));

async function lireMail() {
  if (!outlookDisponible()) {
    return { objet: "TR: demande de prix (échantillon)", expediteur: "contact@exemple.fr", destinataires: "", date: new Date().toISOString(),
             corps: "Bonjour,\n\nPouvez-vous nous chiffrer 1 fer à souder MC50 et 30 courroies sans fin 175 mm ?\n\nSEFI Lorient", pieces_jointes: [] };
  }
  const item = Office.context.mailbox.item;
  const corps = await asyncOffice((cb) => item.body.getAsync(Office.CoercionType.Text, cb));
  const pj = [];
  for (const a of item.attachments || []) {
    const pdf = a.contentType === "application/pdf" || /\.pdf$/i.test(a.name || "");
    if (!pdf || a.isInline || (a.attachmentType && a.attachmentType !== "file")) continue;
    const c = await asyncOffice((cb) => item.getAttachmentContentAsync(a.id, cb));
    if (String(c.format).toLowerCase() === "base64") pj.push({ nom: a.name, type: "application/pdf", contenu_base64: c.content });
  }
  const f = item.from || {};
  return {
    objet: item.subject || "",
    expediteur: [f.displayName, f.emailAddress && "<" + f.emailAddress + ">"].filter(Boolean).join(" "),
    destinataires: (item.to || []).map((t) => t.emailAddress).join(", "),
    date: item.dateTimeCreated ? new Date(item.dateTimeCreated).toISOString() : "",
    corps, pieces_jointes: pj,
  };
}

/* ---------- rendu ---------- */
function champ(label, input) { return [h("label", {}, label), input]; }

function inputLie(obj, cle, opts = {}) {
  const i = h("input", { type: opts.type || "text", value: obj[cle] ?? "", readonly: opts.readonly, min: opts.min, step: opts.step });
  i.addEventListener("input", () => {
    obj[cle] = opts.type === "number" ? (i.value === "" ? null : Number(i.value)) : i.value;
    if (opts.onchange) opts.onchange();
  });
  return i;
}

function renderClient(c) {
  const box = $("client"); box.replaceChildren();
  const badge = h("span", { class: "badge " + c.statut }, { trouve: "trouvé", ambigu: "ambigu", introuvable: "introuvable" }[c.statut] || c.statut);
  box.append(h("div", { class: "grille" },
    champ("Statut", h("div", {}, badge, c.nom_detecte ? "  " + c.nom_detecte : "")),
    champ("Code client", inputLie(c, "code_client")),
    champ("Contact", inputLie(c, "contact")),
    champ("E-mail", inputLie(c, "email")),
    champ("Adresse", inputLie(c, "adresse"))),
    c.commentaire ? h("p", { class: "remarque" }, c.commentaire) : null);
}

const INTENTIONS = { nouveau_devis: "Nouveau devis", mise_a_jour_devis: "Mise à jour d'un devis", variantes_devis_existant: "Variantes d'un devis existant",
                     demande_info_ou_distributeur: "Demande d'information / distributeur", autre: "Autre" };

function renderMeta(p) {
  $("meta").replaceChildren(h("div", { class: "grille" },
    champ("Type", h("div", {}, INTENTIONS[p.intention] || p.intention)),
    champ("Votre réf.", inputLie(p, "reference_client")),
    p.devis_source ? champ("Devis source", h("div", {}, p.devis_source)) : []));
}

function ligneRow(l, lignes, rerender, editable) {
  const tr = h("tr", { class: "s-" + (l.statut || "a_verifier") });
  const prixTd = h("td", { class: "prix" });
  const remTd = h("td", {});
  const majPrix = () => {
    prixTd.replaceChildren(l.prix_unitaire == null ? h("span", { class: "sans-prix" }, "à calculer") : l.prix_unitaire.toLocaleString("fr-FR", { maximumFractionDigits: 4 }) + " €");
    tr.className = "s-" + (l.statut || "a_verifier");
  };
  const invalider = () => {
    l.prix_unitaire = null; l.statut = "a_verifier";
    l.remarque = "Modifié : prix à recalculer (demandez « recalcule les prix » à l'IA).";
    majPrix(); remTd.textContent = l.remarque;
  };
  majPrix();
  remTd.textContent = [l.remarque, l.delai && "Délai : " + l.delai].filter(Boolean).join(" · ");
  remTd.className = "remarque";
  tr.append(
    h("td", {}, l.designation_demandee || "", l.reference_demandee ? h("div", { class: "remarque" }, "réf. " + l.reference_demandee) : null),
    h("td", {}, inputLie(l, "code_article", { onchange: invalider }), h("div", { class: "remarque" }, l.designation_article || "")),
    h("td", { class: "num" }, inputLie(l, "quantite", { type: "number", min: 0, step: "any", onchange: invalider })),
    h("td", { class: "num" }, inputLie(l, "unite", { onchange: invalider })),
    prixTd, remTd,
    editable ? h("td", {}, h("button", { title: "Supprimer la ligne", onclick: () => { lignes.splice(lignes.indexOf(l), 1); rerender(); } }, "✕")) : h("td"));
  return tr;
}

function tableau(titre, lignes, rerender, principal) {
  const t = h("table", {}, h("thead", {}, h("tr", {}, ["Demandé", "Article Divalto", "Qté", "Unité", "Prix unit.", "Remarque", ""].map((x) => h("th", {}, x)))),
              h("tbody", {}, lignes.map((l) => ligneRow(l, lignes, rerender, principal))));
  return h("div", {}, h("h2", {}, titre), h("div", { class: "defile" }, t),
    principal ? h("button", { onclick: () => { lignes.push({ designation_demandee: "", quantite: 1, statut: "a_verifier" }); rerender(); } }, "+ Ajouter une ligne") : null);
}

function renderProposition(p, alertes) {
  state.p = p;
  renderClient(p.client);
  renderMeta(p);
  const g = $("groupes"); g.replaceChildren();
  const rerender = () => renderProposition(state.p, []);
  g.append(tableau("Lignes du devis", p.lignes, rerender, true));
  for (const v of p.variantes || []) g.append(tableau("Variante : " + v.libelle, v.lignes, rerender, false));
  const a = $("alertes"); a.hidden = !(alertes && alertes.length);
  a.replaceChildren(...(alertes || []).map((x) => h("div", {}, "⚠ " + x)));
  $("points").replaceChildren(...(p.points_a_verifier || []).map((x) => h("li", {}, x)));
  $("resultat").hidden = false;
  $("btn-creer").disabled = state.busy || !p.lignes.length;
  resetCreer();
}

/* ---------- actions ---------- */
async function analyser() {
  setBusy(true); statut("Lecture du mail…");
  try {
    const mail = await lireMail();
    statut("Analyse en cours (client, articles, prix dans Divalto)…");
    const r = await api("/analyse", mail);
    state.sid = r.session_id;
    if (r.proposition) { renderProposition(r.proposition, r.alertes); statut("Proposition prête : à relire et corriger avant création."); }
    else { statut((r.alertes || []).join(" ") || "Aucune proposition : traitement manuel.", true); }
  } catch (e) { statut(e.message, true); } finally { setBusy(false); }
}

async function envoyer() {
  const m = $("message").value.trim();
  if (!m || !state.sid) return;
  setBusy(true); statut("L'IA met à jour la proposition…");
  try {
    const r = await api("/sessions/" + state.sid + "/corriger", { message: m, proposition_actuelle: state.p });
    $("message").value = "";
    if (r.proposition) { renderProposition(r.proposition, r.alertes); statut("Proposition mise à jour."); }
    else statut((r.alertes || []).join(" ") || "Pas de nouvelle proposition.", true);
  } catch (e) {
    statut(e.status === 404 ? "Session expirée : relancez l'analyse." : e.message, true);
  } finally { setBusy(false); }
}

let armeTimer = null;
function resetCreer() { clearTimeout(armeTimer); $("btn-creer").textContent = "Créer le devis dans Divalto"; $("btn-creer").dataset.arme = ""; }

async function creer() {
  const b = $("btn-creer");
  if (!b.dataset.arme) {   // 1er clic : confirmation inline (pas de boîte de dialogue native)
    b.dataset.arme = "1"; b.textContent = "Confirmer la création ?";
    armeTimer = setTimeout(resetCreer, 6000);
    return;
  }
  resetCreer(); setBusy(true); statut("Création du devis dans Divalto…");
  try {
    const r = await api("/sessions/" + state.sid + "/creer-devis", { proposition: state.p });
    statut("Devis créé : n° " + r.devis.numero + (r.devis.simule ? " (SIMULATION)" : "") + ". Vérifiez-le dans Divalto.");
  } catch (e) { statut(e.message, true); } finally { setBusy(false); }
}

function init() {
  $("jeton").value = getToken();
  $("jeton").addEventListener("change", () => setToken($("jeton").value));
  $("btn-analyse").addEventListener("click", analyser);
  $("btn-envoyer").addEventListener("click", envoyer);
  $("btn-creer").addEventListener("click", creer);
  $("message").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.ctrlKey) envoyer(); });
  if (!getToken()) $("reglages").open = true;
}

if (typeof Office !== "undefined" && !window.__noOffice && Office.onReady) Office.onReady(init); else document.addEventListener("DOMContentLoaded", init);
