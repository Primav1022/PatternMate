const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Access-Control-Allow-Headers": "content-type",
};

const JOB_HOST = "https://patternmate-job.internal";

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    try {
      return await route(request, env, ctx);
    } catch (error) {
      return json({ ok: false, error: String(error && error.message ? error.message : error) }, 500);
    }
  },
};

async function route(request, env, ctx) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/$/, "") || "/";

  if (path === "/health" && request.method === "GET") {
    return json({
      ok: true,
      chat_ready: Boolean(env.MODEL_API_KEY && env.MODEL_BASE_URL),
      print_ready: Boolean(env.MODEL_API_KEY && env.MODEL_BASE_URL),
      image_backend: "api",
      image_api_ready: Boolean(env.MODEL_API_KEY),
      image_model: env.IMAGE_MODEL_NAME || "gpt-image-2",
    });
  }

  if (path === "/catalog" && request.method === "GET") return json({ items: catalogItems() });

  if (path === "/design/conversation" && request.method === "POST") {
    return json(await conversation(await request.json(), env));
  }

  if (path === "/analyze" && request.method === "POST") {
    const body = await request.json();
    const result = await conversation({
      messages: [{ role: "user", content: body.text || "" }],
      language: body.language || "zh",
      intent_version: 0,
      current_intent: {},
      confirmed: {},
    }, env);
    return json(result);
  }

  if (path === "/translate" && request.method === "POST") {
    const body = await request.json();
    const text = await chat(env, "Translate into concise natural English. Return only the translation.", body.text || "");
    return json({ text: text || body.text || "" });
  }

  if (path === "/design-preview/prompt" && request.method === "POST") {
    const body = await request.json();
    return json({ prompt: (body.prompt || "").trim() || designPrompt(body) });
  }

  if (path === "/design-preview/jobs" && request.method === "POST") {
    return enqueue(await request.json(), "design_preview", env, ctx);
  }

  if (path === "/garment-print/jobs" && request.method === "POST") {
    return enqueue(await request.json(), "garment_print", env, ctx);
  }

  const previewJob = path.match(/^\/design-preview\/jobs\/([^/]+)$/);
  if (previewJob && request.method === "GET") return json(await readJob(previewJob[1]) || notFoundJob(previewJob[1]));

  const printJob = path.match(/^\/garment-print\/jobs\/([^/]+)$/);
  if (printJob && request.method === "GET") return json(await readJob(printJob[1]) || notFoundJob(printJob[1]));

  const result = path.match(/^\/results\/([^/]+)$/);
  if (result && request.method === "GET") {
    const image = await caches.default.match(new Request(`${JOB_HOST}/img/${result[1]}`));
    if (!image) return json({ error: "Result not found" }, 404);
    const headers = new Headers(image.headers);
    Object.entries(CORS).forEach(([key, value]) => headers.set(key, value));
    return new Response(image.body, { headers });
  }

  return json({ error: "not found" }, 404);
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json", ...CORS } });
}

function catalogItems() {
  const ids = [
    "C2390077", "C2390270", "C2390279", "C2390303", "C2390726",
    "C2430065", "C2430079", "C2430144", "C2430196", "C2430367",
    "C2431027", "C2431055", "C2490092", "C2490188", "C2490194",
    "C2490252", "C2490257", "C2490260", "C2490278", "C2490320",
    "C2490335", "C2490383", "C2490411", "C2490437",
  ];
  return ids.map((id, index) => {
    const shirt = index % 4 === 0 || id.startsWith("C243");
    return {
      case_id: id,
      category: shirt ? "shirt" : "tshirt",
      original_category: shirt ? "shirt" : "tshirt",
      cover_url: `/reference-images/v1/${id}/cover.jpg`,
      semantics: { fit: shirt ? "regular" : "relaxed", style_tags: shirt ? ["通勤", "衬衫"] : ["休闲", "基础"] },
      base_option_ids: shirt
        ? { silhouette: "shirt.silhouette.regular-fit", placket: "shirt.placket.full", sleeve: "shirt.sleeve.regular" }
        : { neckline: "tshirt.neckline.crew", sleeve: "tshirt.sleeve.set-in" },
    };
  });
}

async function conversation(body, env) {
  const messages = Array.isArray(body.messages) ? body.messages : [];
  const last = [...messages].reverse().find((item) => item.role === "user")?.content || "";
  const language = (body.language || "zh").startsWith("en") ? "English" : "Simplified Chinese";
  const schema = {
    task: "Apparel design assistant. Return JSON only.",
    fields: ["family", "category", "sleeve", "fit", "neckline", "styles", "labels", "assistant_message"],
    allowed: { family: ["tshirt", "shirt", null], sleeve: ["sleeveless", "short", "long", null], fit: ["relaxed", "regular", "fitted", null], neckline: ["v-neck", "crew", "polo", null] },
    conversation: messages,
    assistant_reply_language: language,
  };
  let parsed = {};
  try {
    const content = await chat(env, "Return JSON only.", JSON.stringify(schema));
    parsed = JSON.parse(String(content || "{}").replace(/^```(?:json)?\s*|\s*```$/g, ""));
  } catch { /* keep fallback */ }
  const intent = { ...(body.current_intent || {}), ...parsed };
  delete intent.assistant_message;
  const assistant = parsed.assistant_message || (language === "English" ? "Noted. Tell me the garment type, fit, or scene you want." : "已记下。可以继续说品类、版型或穿着场景。");
  return {
    intent_version: Number(body.intent_version || 0) + 1,
    assistant_message: last ? assistant : assistant,
    intent,
    confirmed: body.confirmed || {},
    summary: [],
    ui_cards: [],
    suggestion_chips: [],
    unresolved: [],
    facets: [],
    items: [],
    analysis_mode: parsed.assistant_message ? "model" : "rules",
  };
}

async function chat(env, system, user) {
  const base = String(env.MODEL_BASE_URL || "").replace(/\/$/, "");
  const key = env.MODEL_API_KEY;
  const model = env.DESIGN_MODEL_NAME || env.IMAGE_MODEL_NAME;
  if (!base || !key || !user) return "";
  const response = await fetch(`${base}/chat/completions`, {
    method: "POST",
    headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
    body: JSON.stringify({ model, temperature: 0.2, messages: [{ role: "system", content: system }, { role: "user", content: user }] }),
  });
  if (!response.ok) throw new Error(`chat ${response.status}: ${await response.text()}`);
  const body = await response.json();
  return body.choices?.[0]?.message?.content?.trim() || "";
}

function designPrompt(request) {
  const color = request.fabric_color || "#ffffff";
  return `Create one realistic strictly front-facing fashion design preview from the supplied reference garment. The garment body color MUST be exactly ${color}. Show one person from head to hips, centered. Preserve garment construction. ABSOLUTELY NO VISIBLE TEXT. Context: ${JSON.stringify({
    family: request.family, sex: request.sex, intent: request.intent, selections: request.selections, fabric: request.material_label, process: request.process_label, measurements: request.measurements_cm,
  })}`;
}

function printPrompt(request) {
  const history = (request.history || []).join("; ");
  if (request.process === "tie-dye") {
    return `Apply traditional batik / wax-resist (蜡染) and handmade tie-dye (扎染) as a fabric-level dye texture on the supplied garment preview, not a printed graphic sticker. Keep person, pose, garment silhouette and construction unchanged. Show wax-crackle, indigo bleed, and irregular dye diffusion on the cloth. Latest request: ${request.prompt || ""}. Previous: ${history}`;
  }
  return `Perform a minimal localized print-only edit on the supplied garment preview. Keep person, pose, garment color and silhouette unchanged. Latest request: ${request.prompt || ""}. Previous: ${history}`;
}

async function enqueue(input, kind, env, ctx) {
  const job_id = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
  const job = { job_id, status: "queued", progress: 8, stage: "queued", result_urls: [], kind };
  await putJob(job);
  ctx.waitUntil(runJob(job_id, input, kind, env));
  return json(job, 202);
}

function notFoundJob(job_id) {
  return { job_id, status: "failed", progress: 0, stage: "failed", error: "job not found", result_urls: [] };
}

async function runJob(job_id, input, kind, env) {
  try {
    await putJob({ job_id, status: "running", progress: 18, stage: "generating_preview", result_urls: [], kind });
    const prompt = kind === "garment_print" ? printPrompt(input) : ((input.prompt || "").trim() || designPrompt(input));
    const reference = await referenceBytes(input, kind, env);
    const preview = await imageCall(env, reference ? "edit" : "generate", prompt, reference);
    await putImage(`${job_id}-0.png`, preview);
    const job = {
      job_id, status: "succeeded", progress: 100, stage: "completed", kind, prompt,
      result_urls: [`/results/${job_id}-0.png`],
      preview_url: `/results/${job_id}-0.png`,
    };
    if (kind === "garment_print") {
      await putJob({ ...job, status: "running", progress: 62, stage: "generating_production_artwork" });
      const artwork = await imageCall(env, "generate", input.process === "tie-dye"
        ? `Independent batik / wax-resist fabric texture swatch for production reference. Wax crackle, handmade tie-dye bleed, no person or garment mockup. User direction: ${input.prompt || ""}`
        : `Independent production print artwork, no person or garment mockup. User direction: ${input.prompt || ""}`, null);
      await putImage(`${job_id}-1.png`, artwork);
      job.result_urls.push(`/results/${job_id}-1.png`);
      job.production_asset = { url: `/results/${job_id}-1.png`, mode: "motif", format: "PNG", width_px: 1024, height_px: 1024, dpi: 300, color_space: "sRGB", transparent: false };
      job.status = "succeeded"; job.progress = 100; job.stage = "completed";
    }
    await putJob(job);
  } catch (error) {
    await putJob({ job_id, status: "failed", progress: 0, stage: "failed", kind, result_urls: [], error: String(error && error.message ? error.message : error) });
  }
}

async function referenceBytes(input, kind, env) {
  if (kind === "garment_print") {
    const src = input.inspiration_image_data_url || input.source_preview_url || "";
    if (src.startsWith("data:")) return dataUrlBytes(src);
    if (src) return (await fetch(src)).arrayBuffer();
    return null;
  }
  const caseId = input.case_id;
  if (!caseId) return null;
  const assetBase = String(env.ASSET_BASE || "").replace(/\/$/, "");
  for (const name of ["cover.jpg", "cover.png", "cover.jpeg", "cover.webp", "thumb.jpg"]) {
    const response = await fetch(`${assetBase}/reference-images/v1/${caseId}/${name}`);
    if (response.ok) return response.arrayBuffer();
  }
  return null;
}

function dataUrlBytes(value) {
  const match = String(value).split(",", 2);
  const binary = atob(match[1] || "");
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

async function imageCall(env, kind, prompt, imageBytes) {
  const base = String(env.MODEL_BASE_URL || "").replace(/\/$/, "");
  const key = env.MODEL_API_KEY;
  const model = env.IMAGE_MODEL_NAME || "gpt-image-2";
  if (!base || !key) throw new Error("image api is not configured");
  const headers = { authorization: `Bearer ${key}` };
  let response;
  if (kind === "generate" || !imageBytes) {
    response = await fetch(`${base}/images/generations`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ model, prompt, n: 1, size: "1024x1024", quality: "medium", response_format: "b64_json" }),
    });
  } else {
    const form = new FormData();
    form.set("model", model);
    form.set("prompt", prompt);
    form.set("n", "1");
    form.set("size", "1024x1024");
    form.set("quality", "medium");
    form.set("response_format", "b64_json");
    form.set("image", new Blob([imageBytes], { type: "image/png" }), "reference.png");
    response = await fetch(`${base}/images/edits`, { method: "POST", headers, body: form });
  }
  if (!response.ok) throw new Error(`image api ${response.status}: ${(await response.text()).slice(0, 400)}`);
  const body = await response.json();
  const item = body.data?.[0] || {};
  if (item.b64_json) {
    const raw = String(item.b64_json).includes(",") ? String(item.b64_json).split(",")[1] : item.b64_json;
    const binary = atob(raw);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }
  if (item.url) return new Uint8Array(await (await fetch(item.url)).arrayBuffer());
  throw new Error("image api returned no image");
}

async function putJob(job) {
  await caches.default.put(new Request(`${JOB_HOST}/job/${job.job_id}`), new Response(JSON.stringify(job), { headers: { "content-type": "application/json", "cache-control": "max-age=7200" } }));
}

async function readJob(job_id) {
  const hit = await caches.default.match(new Request(`${JOB_HOST}/job/${job_id}`));
  return hit ? hit.json() : null;
}

async function putImage(name, bytes) {
  await caches.default.put(new Request(`${JOB_HOST}/img/${name}`), new Response(bytes, { headers: { "content-type": "image/png", "cache-control": "max-age=86400" } }));
}
