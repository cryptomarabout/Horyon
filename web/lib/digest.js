// Parse a stored digest (Telegram-HTML bullets) into structured items.
// Each line looks like:  • <b>Title</b> — Body sentence(s). <a href="url">🔗</a>

const ENTITIES = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
  "&apos;": "'",
  "&nbsp;": " ",
};

function decode(s) {
  return s.replace(/&[a-z#0-9]+;/gi, (m) => ENTITIES[m] ?? m);
}

function stripTags(s) {
  return s.replace(/<[^>]+>/g, "");
}

export function parseDigest(content) {
  const lines = (content || "").replace(/\r/g, "").split("\n");
  const bullets = [];
  let heading = "";

  for (const line of lines) {
    const t = line.trim();
    if (!t) continue;
    if (t.startsWith("#")) {
      heading = t.replace(/^#+\s*/, "").trim();
      continue;
    }
    if (!t.startsWith("•")) continue;

    const body0 = t.replace(/^•\s*/, "");
    const titleMatch = body0.match(/<b>([\s\S]*?)<\/b>/i);
    const linkMatch = body0.match(/<a[^>]*href="([^"]+)"/i);

    const rest = body0
      .replace(/<b>[\s\S]*?<\/b>/i, "")
      .replace(/<a[^>]*>[\s\S]*?<\/a>/gi, "")
      .replace(/^\s*[—–-]+\s*/, "")
      .trim();

    const title = decode(stripTags(titleMatch?.[1] ?? "")).trim();
    const body = decode(stripTags(rest)).replace(/\s+/g, " ").trim();

    bullets.push({
      title,
      body,
      link: linkMatch?.[1],
      hack: /🚨/.test(t),
    });
  }

  return { heading, bullets };
}
