#!/usr/bin/env python3
"""
Sync blog posts from Shopify into the Renoxa static website.

For every *visible* (published) article in the Shopify blog it:
  - writes  post-<handle>.html  using the same design as the existing blog pages
  - adds a card to the homepage blog row (between the SHOPIFY-BLOG markers)
  - adds the URL to sitemap.xml and llms.txt (between the SHOPIFY-BLOG markers)
  - deletes post-*.html pages it generated earlier whose article was hidden/deleted

Hand-written blog-*.html pages are never touched.

Required environment variables:
  SHOPIFY_STORE_DOMAIN       e.g. bablu-bxbvivbh.myshopify.com
  SHOPIFY_STOREFRONT_TOKEN   Storefront API access token (read content scope)
    or SHOPIFY_ADMIN_TOKEN    Admin API token "shpat_..." (needs read_content scope)
Optional:
  SHOPIFY_BLOG_HANDLE        blog handle, default "news"
  SHOPIFY_API_VERSION        default "2025-01"

Uses only the Python standard library.
"""
import html, json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://renoxa.in/"
MARK = '<meta name="generator" content="renoxa-shopify-blog-sync" />'
WA = "https://wa.me/919310515789?text="
LOGO = "https://cdn.shopify.com/s/files/1/0778/9474/6301/files/Untitled_design_32.png?v=1781342264"
e = lambda s: html.escape(s or "", quote=True)

QUERY = """
query Articles($handle: String!, $after: String) {
  blog(handle: $handle) {
    title
    articles(first: 50, after: $after, sortKey: PUBLISHED_AT, reverse: true) {
      pageInfo { hasNextPage endCursor }
      nodes {
        handle title excerpt contentHtml content publishedAt tags
        image { url altText }
        seo { title description }
        authorV2 { name }
      }
    }
  }
}"""


ADMIN_BLOG_QUERY = """
query Blog($q: String!) { blogs(first: 1, query: $q) { nodes { id handle } } }"""

ADMIN_QUERY = """
query Articles($q: String!, $after: String) {
  articles(first: 50, after: $after, query: $q, sortKey: PUBLISHED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    nodes {
      handle title summary body publishedAt isPublished tags
      blog { handle }
      image { url altText }
      author { name }
      seoTitle: metafield(namespace: "global", key: "title_tag") { value }
      seoDesc: metafield(namespace: "global", key: "description_tag") { value }
    }
  }
}"""


def _post(url, headers, query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if data.get("errors"):
        sys.exit(f"Shopify API error: {data['errors']}")
    return data["data"]


def fetch_articles_admin(domain, token, handle, version):
    """Admin API path (token starts with shpat_). Needs the read_content scope."""
    url = f"https://{domain}/admin/api/{version}/graphql.json"
    hdr = {"X-Shopify-Access-Token": token}
    blogs = _post(url, hdr, ADMIN_BLOG_QUERY, {"q": f"handle:{handle}"})["blogs"]["nodes"]
    blogs = [b for b in blogs if b["handle"] == handle]
    if not blogs:
        sys.exit(f"No blog with handle '{handle}' found. Check SHOPIFY_BLOG_HANDLE.")
    blog_id = blogs[0]["id"].rsplit("/", 1)[-1]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out, after = [], None
    while True:
        conn = _post(url, hdr, ADMIN_QUERY, {"q": f"blog_id:{blog_id}", "after": after})["articles"]
        for a in conn["nodes"]:
            # only visible posts whose (scheduled) publish time has passed
            if not a["isPublished"] or not a["publishedAt"] or a["publishedAt"] > now:
                continue
            if (a.get("blog") or {}).get("handle") != handle:
                continue
            out.append({
                "handle": a["handle"], "title": a["title"],
                "excerpt": plain(a.get("summary") or "", 300) if a.get("summary") else "",
                "contentHtml": a.get("body") or "",
                "content": plain(a.get("body") or "", 2000),
                "publishedAt": a["publishedAt"], "tags": a.get("tags") or [],
                "image": a.get("image"),
                "seo": {"title": (a.get("seoTitle") or {}).get("value"),
                        "description": (a.get("seoDesc") or {}).get("value")},
                "authorV2": a.get("author"),
            })
        if not conn["pageInfo"]["hasNextPage"]:
            return out
        after = conn["pageInfo"]["endCursor"]


def fetch_articles():
    domain = os.environ["SHOPIFY_STORE_DOMAIN"].strip().replace("https://", "").strip("/")
    token = (os.environ.get("SHOPIFY_ADMIN_TOKEN") or os.environ.get("SHOPIFY_STOREFRONT_TOKEN") or "").strip()
    if not token:
        sys.exit("Set SHOPIFY_ADMIN_TOKEN or SHOPIFY_STOREFRONT_TOKEN.")
    handle = os.environ.get("SHOPIFY_BLOG_HANDLE", "news").strip()
    version = os.environ.get("SHOPIFY_API_VERSION", "2025-01").strip()
    if token.startswith("shpat_"):
        return fetch_articles_admin(domain, token, handle, version)
    url = f"https://{domain}/api/{version}/graphql.json"
    out, after = [], None
    while True:
        body = json.dumps({"query": QUERY, "variables": {"handle": handle, "after": after}}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "X-Shopify-Storefront-Access-Token": token,
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        if data.get("errors"):
            sys.exit(f"Shopify API error: {data['errors']}")
        blog = data["data"]["blog"]
        if blog is None:
            sys.exit(f"No blog with handle '{handle}' found. Check SHOPIFY_BLOG_HANDLE.")
        conn = blog["articles"]
        out += conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return out
        after = conn["pageInfo"]["endCursor"]


def slugify(h):
    return re.sub(r"[^a-z0-9-]+", "-", (h or "").lower()).strip("-") or "post"


def plain(text, n=160):
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def template_parts():
    """Reuse the head CSS, top bar and footer of an existing hand-written post."""
    src = open(os.path.join(ROOT, "blog-back-pain.html"), encoding="utf-8").read()
    head_top = src[: src.index("<title>")]
    css = src[src.index('<link rel="preconnect"'): src.index("</head>")]
    css = css.replace("</style>", EXTRA_CSS + "</style>")
    topbar = src[src.index('<div class="topbar">'): src.index('<article class="article">')]
    footer = src[src.index("<footer>"): src.index('<script type="application/ld+json">')]
    wa_svg = re.search(r'(<svg viewBox="0 0 24 24" aria-hidden="true">.*?</svg>)', src, re.S).group(1)
    return head_top, css, topbar, footer, wa_svg


EXTRA_CSS = """
  /* rich text coming from the Shopify editor */
  .prose h3{font-size:20px;font-weight:800;color:#fff;margin:30px 0 12px;}
  .prose h4{font-size:17px;font-weight:700;color:#fff;margin:24px 0 10px;}
  .prose ol{margin:0 0 22px 22px;color:var(--body);font-size:17px;line-height:1.7;}
  .prose ol li{padding-left:6px;margin-bottom:10px;}
  .prose ol li::before{display:none;}
  .prose a{color:#7fd3e4;text-decoration:underline;}
  .prose img{max-width:100%;height:auto;border-radius:16px;margin:10px 0 22px;}
  .prose blockquote{margin:0 0 22px;padding:14px 20px;border-left:3px solid var(--teal-bright);background:rgba(255,255,255,.04);border-radius:0 12px 12px 0;color:var(--body);}
  .prose table{width:100%;border-collapse:collapse;margin:0 0 22px;font-size:15px;}
  .prose th,.prose td{border:1px solid var(--glass-brd);padding:10px;text-align:left;color:var(--body);}
  .prose iframe,.prose video{max-width:100%;border-radius:16px;}
"""


def build_page(a, others, parts):
    head_top, css, topbar, footer, wa_svg = parts
    slug = "post-" + slugify(a["handle"])
    url = SITE + slug + ".html"
    title = a["title"]
    seo_title = (a.get("seo") or {}).get("title") or title
    desc = (a.get("seo") or {}).get("description") or a.get("excerpt") or plain(a.get("content"))
    desc = plain(desc, 160)
    img = (a.get("image") or {}).get("url") or LOGO
    alt = (a.get("image") or {}).get("altText") or title
    tags = a.get("tags") or []
    section = tags[0] if tags else "Health & Recovery"
    author = ((a.get("authorV2") or {}).get("name") or "").strip() or "Renoxa Physiotherapy"
    words = len(re.findall(r"\w+", a.get("content") or ""))
    minutes = max(1, round(words / 200))
    pub = a["publishedAt"]
    pub_human = datetime.fromisoformat(pub.replace("Z", "+00:00")).strftime("%-d %b %Y")
    wa = WA + urllib.parse.quote(f"Hi Renoxa, I read your article \"{title}\" and would like to book an assessment.")
    more = "\n".join(f'    <a href="{o["slug"]}.html">{e(o["title"])}</a>' for o in others[:3])

    ld = [
        {"@context": "https://schema.org", "@type": "BlogPosting", "headline": title, "description": desc,
         "image": img, "articleSection": section, "keywords": ", ".join(tags) or None,
         "datePublished": pub, "dateModified": pub,
         "author": {"@type": "Person", "name": author} if author != "Renoxa Physiotherapy"
                   else {"@type": "Organization", "name": author, "url": SITE},
         "publisher": {"@type": "Organization", "name": "Renoxa Physiotherapy",
                       "logo": {"@type": "ImageObject", "url": LOGO}},
         "mainEntityOfPage": {"@type": "WebPage", "@id": url}, "url": url},
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": SITE},
            {"@type": "ListItem", "position": 2, "name": "Blog", "item": SITE + "#blog"},
            {"@type": "ListItem", "position": 3, "name": title, "item": url}]},
    ]
    if ld[0]["keywords"] is None:
        del ld[0]["keywords"]
    ld_html = "\n".join('<script type="application/ld+json">\n' + json.dumps(o, ensure_ascii=False, indent=2) + "\n</script>" for o in ld)

    page = f"""{head_top}<title>{e(seo_title)} · Renoxa Physiotherapy</title>
<meta name="description" content="{e(desc)}" />
<meta name="robots" content="index, follow" />
<link rel="canonical" href="{url}" />
<meta name="theme-color" content="#0a2e38" />
{MARK}

<meta property="og:type" content="article" />
<meta property="og:site_name" content="Renoxa Physiotherapy" />
<meta property="article:section" content="{e(section)}" />
<meta property="article:published_time" content="{e(pub)}" />
<meta property="og:title" content="{e(seo_title)} | Renoxa Physiotherapy Blog" />
<meta property="og:description" content="{e(desc)}" />
<meta property="og:image" content="{e(img)}" />
<meta property="og:url" content="{url}" />
<meta property="og:locale" content="en_IN" />

<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="{e(seo_title)} | Renoxa Physiotherapy Blog" />
<meta name="twitter:description" content="{e(desc)}" />
<meta name="twitter:image" content="{e(img)}" />
{css}</head>
<body>
  {topbar}<article class="article">
    <a class="back" href="index.html#blog"><svg viewBox="0 0 24 24"><path d="M19 12H5M11 6l-6 6 6 6"/></svg>Back to all articles</a>
    <div class="eyebrow">{e(section)}</div>
    <h1>{e(title)}</h1>
    <div class="meta"><span>{minutes} min read</span><span class="dot"></span><span>{e(author)}</span><span class="dot"></span><time datetime="{e(pub)}">{pub_human}</time></div>
    <img class="cover" src="{e(img)}" alt="{e(alt)}" onerror="this.style.opacity='0.4'">
    <div class="prose">
{a.get("contentHtml") or ""}
      <div class="note"><strong>A quick note:</strong> This article is general guidance, not a personal diagnosis. Pain that is severe, sudden, or not improving should be checked by a physiotherapist or doctor.</div>
    </div>

    <div class="cta-box">
      <h3>Want a plan made for your body?</h3>
      <p>Tell us what's bothering you and we'll take it from there on WhatsApp.</p>
      <a href="{e(wa)}" target="_blank" rel="noopener">{wa_svg}Book on WhatsApp</a>
    </div>
  </article>

  <div class="more">
    <h4>Keep reading</h4>
{more}
  </div>

  {footer}{ld_html}
</body>
</html>
"""
    return slug, page, {"slug": slug, "title": title, "excerpt": plain(a.get("excerpt") or desc, 140),
                        "img": img, "alt": alt, "section": section, "url": url}


def replace_between(text, start, end, inner):
    i, j = text.index(start), text.index(end)
    return text[: i + len(start)] + inner + text[j:]


def main():
    articles = fetch_articles()
    print(f"Fetched {len(articles)} visible article(s) from Shopify")
    parts = template_parts()

    # list of all posts for "Keep reading": Shopify posts first, then the hand-written ones
    static = [{"slug": s, "title": t} for s, t in [
        ("blog-back-pain", "5 Stretches to Ease Lower Back Pain at Home"),
        ("blog-knee-surgery", "What to Expect When Recovering After Knee Surgery"),
        ("blog-posture", "Desk Job? 6 Posture Habits That Save Your Neck"),
        ("blog-sports-injury", "Returning to Sport Safely After an Injury"),
        ("blog-frozen-shoulder", "Frozen Shoulder: Why It Happens and How to Treat It")]]
    listing = [{"slug": "post-" + slugify(a["handle"]), "title": a["title"]} for a in articles] + static

    cards, keep = [], set()
    for a in articles:
        slug = "post-" + slugify(a["handle"])
        others = [p for p in listing if p["slug"] != slug]
        slug, page, card = build_page(a, others, parts)
        path = os.path.join(ROOT, slug + ".html")
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if old != page:
            open(path, "w", encoding="utf-8").write(page)
            print("  wrote", slug + ".html")
        keep.add(slug + ".html")
        cards.append(card)

    # remove pages for articles that were hidden or deleted in Shopify (only ones we generated)
    for f in os.listdir(ROOT):
        if f.startswith("post-") and f.endswith(".html") and f not in keep:
            p = os.path.join(ROOT, f)
            if MARK in open(p, encoding="utf-8").read():
                os.remove(p)
                print("  removed", f)

    arrow = '<svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg>'
    home_cards = "".join(f"""
      <a class="bpost" href="{c['slug']}.html">
        <img class="bpost-img" src="{e(c['img'])}" alt="{e(c['alt'])}" loading="lazy" onerror="this.style.opacity='0.4'">
        <div class="bpost-body">
          <span class="bpost-cat">{e(c['section'])}</span>
          <div class="bpost-title">{e(c['title'])}</div>
          <p class="bpost-excerpt">{e(c['excerpt'])}</p>
          <span class="bpost-more">Read article {arrow}</span>
        </div>
      </a>""" for c in cards)
    edits = {
        "index.html": ("<!-- SHOPIFY-BLOG:START (auto-generated from Shopify, do not edit by hand) -->",
                       "<!-- SHOPIFY-BLOG:END -->", home_cards + "\n      "),
        "sitemap.xml": ("<!-- SHOPIFY-BLOG:START -->", "<!-- SHOPIFY-BLOG:END -->",
                        "".join(f"\n  <url>\n    <loc>{c['url']}</loc>\n    <lastmod>{a['publishedAt'][:10]}</lastmod>\n    <changefreq>monthly</changefreq>\n    <priority>0.7</priority>\n  </url>"
                                for c, a in zip(cards, articles)) + "\n  "),
        "llms.txt": ("<!-- SHOPIFY-BLOG:START -->", "<!-- SHOPIFY-BLOG:END -->",
                     "".join(f"\n- [{c['title']}]({c['url']})" for c in cards) + "\n"),
    }
    for fname, (start, end, inner) in edits.items():
        p = os.path.join(ROOT, fname)
        s = open(p, encoding="utf-8").read()
        new = replace_between(s, start, end, inner)
        if new != s:
            open(p, "w", encoding="utf-8").write(new)
            print("  updated", fname)
    print("Done.")


if __name__ == "__main__":
    main()
