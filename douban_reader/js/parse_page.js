/*!
 * 解析豆瓣阅读器"当前页"的 DOM，返回结构化数据。
 *
 * 由 douban_reader.parsing.PageParser 通过 execute_script 注入。
 * 本文件**不含任何选择器字面量**：全部经 arguments[0] 传入
 * （唯一定义处是 douban_reader/selectors.py），页面改版时只改 Python 一处。
 *
 * 注意：脚本必须以顶层 return 结束。
 * chromedriver 只会取脚本顶层 return 的值；若把逻辑包进 IIFE 再调用
 * （`(function(){})(...)`），返回值会被丢弃，Python 侧只能拿到 None。
 *
 * 入参 cfg（由 douban_reader.parsing.build_js_config 生成，缺一项即报 bad_config）:
 *   readerRoot    阅读器根节点选择器（错误诊断用）
 *   anyPage       "任意页容器"选择器（错误诊断用）
 *   currentPage   当前页元素候选选择器数组，按顺序尝试
 *   pageContainer 兜底页容器选择器
 *   pageAttr      页码属性名
 *   content       正文容器候选选择器数组
 *   title         标题候选选择器数组
 *   illusClass    插图段落的类名关键字
 *   illusImage    插图 img 选择器
 *   illusLegend   图注选择器
 *
 * 返回:
 *   {page, title, paragraphs:[{type:'title'|'text'|'image', text?, src?, legend?}]}
 *   解析不到页面时返回 {error:'no_page', ark_exists, page_total} 供 Python 侧出诊断信息。
 */
const cfg = arguments[0] || {};

const required = [
    'readerRoot', 'anyPage', 'currentPage', 'pageContainer', 'pageAttr',
    'content', 'title', 'illusClass', 'illusImage', 'illusLegend'
];
const missing = required.filter((key) => cfg[key] === undefined || cfg[key] === null);
if (missing.length) {
    return {error: 'bad_config', missing: missing};
}

const pick = (root, list) => {
    for (const sel of list) {
        const el = root.querySelector(sel);
        if (el) return el;
    }
    return null;
};

// ---- 1. 定位当前页 ----
let page = null;
for (const sel of cfg.currentPage) {
    page = document.querySelector(sel);
    if (page) break;
}
if (!page) {
    for (const candidate of document.querySelectorAll(cfg.pageContainer)) {
        const rect = candidate.getBoundingClientRect();
        if (rect.width > 50 && rect.height > 50
            && rect.left > -300 && rect.left < 600) {
            page = candidate;
            break;
        }
    }
}
if (!page) {
    return {
        error: 'no_page',
        ark_exists: !!document.querySelector(cfg.readerRoot),
        page_total: document.querySelectorAll(cfg.anyPage).length,
    };
}

// ---- 2. 逐段解析正文 ----
const paragraphs = [];
const content = pick(page, cfg.content);
if (content) {
    for (const node of content.children) {
        if (node.tagName !== 'P') continue;
        const cls = node.className || '';

        if (cls.indexOf(cfg.illusClass) >= 0) {
            const img = node.querySelector(cfg.illusImage);
            const legendEl = node.querySelector(cfg.illusLegend);
            const src = img
                ? (img.getAttribute('data-orig-src') || img.getAttribute('src') || '')
                : '';
            const legend = legendEl ? (legendEl.textContent || '').trim() : '';
            if (src || legend) {
                paragraphs.push({type: 'image', src: src, legend: legend});
            }
            continue;
        }

        const text = (node.textContent || '')
            .replace(/[\r\n\t]+/g, ' ')
            .replace(/\s{2,}/g, ' ')
            .trim();
        if (!text) continue;
        paragraphs.push({
            type: cls.indexOf('headline') >= 0 ? 'title' : 'text',
            text: text,
        });
    }
}

// ---- 3. 页标题 ----
const titleEl = pick(page, cfg.title);
return {
    page: parseInt(page.getAttribute(cfg.pageAttr) || '0', 10),
    title: titleEl ? (titleEl.textContent || '').trim() : '',
    paragraphs: paragraphs,
};
