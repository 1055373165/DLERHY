// Minicharts card. Build with the official assets/build.py from
// https://april-zhu.com/minicharts and run assets/check.py on the result.

const bookFlowPaper = '#fffaf0';
const bookFlowMuted = '#5d584d';

function bookFlowShape(points, color) {
  flat(points, color);
  lid(points, 1.15);
}

function bookFlowRows(x, y, widths, color = bookFlowMuted) {
  rule(widths.map((width, index) => {
    const rowY = y + index * 5;
    return [[x, rowY], [x + width, rowY]];
  }), color, 0.85, 0.74);
}

function bookFlowSheet(x, y, width, height, color = bookFlowPaper) {
  const fold = 8;
  bookFlowShape([
    [x, y], [x + width - fold, y], [x + width, y + fold],
    [x + width, y + height], [x, y + height],
  ], color);
  line([[x + width - fold, y], [x + width - fold, y + fold], [x + width, y + fold]], {
    w: 0.85, col: bookFlowMuted, over: 0,
  });
  bookFlowRows(x + 7, y + 13, [width - 17, width - 19, width - 15, width - 23]);
}

function bookFlowShadow(x, y, width) {
  r0.shadow(x, y, width, 4, 0.14);
}

function bookFlowCheck(x, y, color = INK) {
  line([[x - 5, y], [x - 1, y + 4], [x + 7, y - 5]], {w: 1.55, col: color, over: 0});
}

function bookFlowSource(x, y) {
  bookFlowShadow(x, y + 24, 39);
  bookFlowSheet(x - 30, y - 21, 35, 42, WASH.sky);
  bookFlowShape(rrect(x - 5, y - 23, 36, 45, 3), WASH.wood);
  line([[x + 1, y - 21], [x + 1, y + 21]], {w: 0.85, col: INK, over: 0});
  bookFlowSheet(x + 7, y - 18, 27, 34);
  flat([[x + 17, y + 16], [x + 23, y + 16], [x + 23, y + 23], [x + 20, y + 21], [x + 17, y + 23]], WASH.rose);
}

function bookFlowFolder(x, y) {
  bookFlowShadow(x, y + 25, 43);
  bookFlowShape([
    [x - 35, y + 21], [x - 38, y - 14], [x - 36, y - 21], [x - 13, y - 21],
    [x - 8, y - 15], [x + 32, y - 15], [x + 35, y + 21],
  ], WASH.tan);
  bookFlowSheet(x - 25, y - 27, 30, 35);
  bookFlowSheet(x + 2, y - 25, 26, 35, WASH.sky);
  bookFlowShape([[x - 40, y - 4], [x + 40, y - 4], [x + 34, y + 22], [x - 34, y + 22]], WASH.gold);
  bookFlowShape(rrect(x - 13, y + 3, 26, 12, 1), bookFlowPaper);
  bookFlowRows(x - 8, y + 9, [16]);
}

function bookFlowPackets(x, y) {
  bookFlowShadow(x, y + 25, 39);
  bookFlowSheet(x - 30, y - 23, 35, 42, WASH.sky);
  bookFlowSheet(x - 6, y - 19, 39, 42, WASH.sage);
  bookFlowShape(rrect(x - 8, y - 24, 14, 9, 2), WASH.cream);
  bookFlowShape(rrect(x + 15, y - 20, 17, 9, 2), WASH.cream);
  TX(x - 2, y - 17, 'EN', {size: 6, anchor: 'middle'});
  TX(x + 24, y - 13, 'ZH', {size: 6, anchor: 'middle'});
  AR([x - 13, y + 11], [x + 8, y + 11], {col: INK, w: 1.0});
}

function bookFlowDraft(x, y) {
  bookFlowShadow(x, y + 24, 38);
  bookFlowSheet(x - 27, y - 26, 38, 47);
  bookFlowShape(rrect(x - 21, y - 16, 21, 10, 1), WASH.sky);
  line([[x - 19, y - 8], [x - 13, y - 12], [x - 8, y - 9], [x - 4, y - 13]], {w: 0.8, col: INK, over: 0});
  line([[x + 15, y + 5], [x + 31, y + 19]], {w: 6, col: INK, over: 0});
  line([[x + 16, y + 6], [x + 30, y + 18]], {w: 3, col: WASH.wood, over: 0});
  bookFlowShape(oval(x + 11, y - 1, 13), WASH.sky);
  flat(oval(x + 11, y - 1, 10), '#e1eae9');
  line([[x + 4, y - 4], [x + 8, y - 8], [x + 13, y - 8]], {w: 1, col: bookFlowPaper, over: 0});
}

function bookFlowChecklist(x, y) {
  bookFlowShadow(x, y + 25, 35);
  bookFlowShape(rrect(x - 29, y - 25, 58, 48, 4), WASH.wood);
  bookFlowShape(rrect(x - 23, y - 20, 46, 38, 2), bookFlowPaper);
  bookFlowShape(rrect(x - 11, y - 28, 22, 10, 2), WASH.stone);
  bookFlowCheck(x - 12, y - 9, '#486a54');
  bookFlowRows(x + 1, y - 9, [14, 12]);
  bookFlowCheck(x - 12, y + 7, '#486a54');
  bookFlowRows(x + 1, y + 7, [13, 15]);
}

function bookFlowReader(x, y) {
  bookFlowShadow(x, y + 23, 44);
  bookFlowShape([[x - 42, y - 17], [x, y - 11], [x + 42, y - 17], [x + 42, y + 14], [x, y + 22], [x - 42, y + 14]], WASH.sage);
  const left = [[x - 38, y - 20], [x - 27, y - 22], [x - 12, y - 20], [x, y - 14], [x, y + 17], [x - 16, y + 10], [x - 28, y + 9], [x - 38, y + 12]];
  const right = left.map(([px, py]) => [2 * x - px, py]);
  bookFlowShape(left, bookFlowPaper);
  bookFlowShape(right, bookFlowPaper);
  bookFlowRows(x - 32, y - 12, [19, 22, 20, 21]);
  bookFlowRows(x + 10, y - 12, [21, 20, 23, 17]);
  flat([[x + 22, y + 11], [x + 27, y + 10], [x + 27, y + 24], [x + 24, y + 21], [x + 22, y + 24]], WASH.rose);
}

function bookFlowProof(x, y) {
  bookFlowShadow(x, y + 24, 36);
  bookFlowSheet(x - 20, y - 25, 38, 47);
  line([[x - 13, y + 5], [x - 8, y + 1], [x - 3, y + 5]], {w: 1.3, col: RED, over: 0});
  line([[x - 13, y + 10], [x + 6, y + 10]], {w: 1.15, col: RED, over: 0});
  bookFlowShape([[x + 26, y - 25], [x + 32, y - 22], [x + 19, y + 12], [x + 12, y + 18], [x + 12, y + 9]], WASH.rose);
  flat([[x + 12, y + 9], [x + 19, y + 12], [x + 12, y + 18]], WASH.tan);
  flat([[x + 12, y + 14], [x + 15, y + 15], [x + 12, y + 18]], INK);
}

function bookFlowTray(x, y) {
  bookFlowShadow(x, y + 22, 40);
  bookFlowShape([[x - 36, y - 8], [x + 28, y - 8], [x + 39, y + 19], [x - 39, y + 19]], WASH.wood);
  bookFlowShape(rrect(x - 23, y - 24, 48, 31, 2), WASH.cream);
  line([[x - 22, y - 22], [x + 1, y - 7], [x + 24, y - 22]], {w: 0.9, col: bookFlowMuted, over: 0});
  bookFlowShape([[x - 39, y + 8], [x - 10, y + 8], [x - 7, y + 14], [x + 7, y + 14], [x + 10, y + 8], [x + 39, y + 8], [x + 37, y + 20], [x - 37, y + 20]], WASH.tan);
  flat(oval(x + 22, y - 21, 5), RED);
}

card({
  id: 'book-agent-flow',
  name: '数据流图',
  of: '从原稿到中文阅读版',
  note: '从左上角沿箭头阅读。点选纸张、文件夹或书本，查看各环节的输入和产物。',
  draw() {
    const stages = [
      {x: 85, y: 77, title: '导入原稿', output: 'PDF · EPUB · 网页', art: bookFlowSource,
        note: '上传 PDF 或 EPUB，或抓取网页正文和样式。导入产出文档记录及源文件。'},
      {x: 260, y: 77, title: '解析与体检', output: '章节 · 结构块 · 句子', art: bookFlowFolder,
        note: '提取章节、标题、表格、图像和代码等结构。解析体检记录结构异常，句子用于后续分包。'},
      {x: 435, y: 77, title: '分包翻译', output: '译文包 · 审校问题', art: bookFlowPackets,
        note: '将句子与术语、上下文一起发给模型。保存译文包和用量记录，并运行规则或模型审校。'},
      {x: 435, y: 210, title: '导出与质检', output: '导出草稿 · 质检报告', art: bookFlowDraft,
        note: '生成导出草稿。试译和全书流程检查结构、图表、表格、代码、术语、忠实度和排版。'},
      {x: 260, y: 210, title: '判定质量', output: '问题位置 · 严重程度', art: bookFlowChecklist,
        note: '以质检报告判断交付状态。需修复的问题带有证据、定位、严重程度和修复建议。'},
      {x: 85, y: 210, title: '阅读与下载', output: '中文阅读版 · 多格式', art: bookFlowReader,
        note: '在书库阅读或下载。试译可以先读前两个可选章节，全书翻译可复用这些章节的译文包。'},
      {x: 260, y: 333, title: '定向修复', output: '重译受影响的包', art: bookFlowProof,
        note: '在预算内尝试修正结构、改译或重译受影响的包，随后重新导出并复检。保留达到验收条件的修复。'},
      {x: 85, y: 333, title: '人工处理', output: '待处理问题', art: bookFlowTray,
        note: '自动修复后仍有阻断问题时，生成待处理条目，由读者或维护者查看。'},
    ];

    const main = {col: '#625c50', w: 1.3};
    const returnPath = {col: RED, w: 1.35};
    AR([130, 77], [213, 77], main);
    AR([306, 77], [392, 77], main);
    AR([435, 154], [435, 178], main);
    AR([392, 210], [302, 210], main);
    AR([225, 210], [131, 210], main);
    AR([260, 285], [260, 303], returnPath);
    AR([218, 333], [131, 333], returnPath);
    HVH([301, 333], [475, 210], 498, returnPath);

    TX(178, 199, '可交付', {size: 13.5, anchor: 'middle'});
    TX(273, 300, '需修复', {size: 13.5});
    TX(175, 323, '仍有阻断', {size: 13.5, anchor: 'middle'});
    TX(402, 322, '重新导出并复检', {size: 13.5, anchor: 'middle'});

    for (const {x, y, title, output, art, note} of stages) {
      PT(note, () => {
        art(x, y);
        TX(x, y + 48, title, {size: 18, anchor: 'middle'});
        TX(x, y + 68, output, {size: 13.5, anchor: 'middle', col: bookFlowMuted});
      }, {lift: [0, -3]});
    }
  },
});
