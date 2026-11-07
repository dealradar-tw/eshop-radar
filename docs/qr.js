// 極簡 QR Code 產生器（位元組模式、糾錯等級 M、版本 1～10，最多 213 位元組）。不依賴任何外部資源。
// 用法：const m = qrMatrix("文字"); → m[row][col] 為 true 表示黑色模組。
(function (g) {
  // [每區塊糾錯碼字數, [[區塊數, 每區塊資料碼字數], ...]]
  const EC = [null,
    [10, [[1, 16]]], [16, [[1, 28]]], [26, [[1, 44]]], [18, [[2, 32]]], [24, [[2, 43]]],
    [16, [[4, 27]]], [18, [[4, 31]]], [22, [[2, 38], [2, 39]]], [22, [[3, 36], [2, 37]]], [26, [[4, 43], [1, 44]]]];
  const ALIGN = [null, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]];
  const EXP = new Array(512), LOG = new Array(256);
  for (let i = 0, x = 1; i < 255; i++) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 256) x ^= 0x11d; }
  for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
  const gmul = (a, b) => (a && b) ? EXP[LOG[a] + LOG[b]] : 0;

  function rsGen(n) {
    let p = [1];
    for (let i = 0; i < n; i++) {
      const q = new Array(p.length + 1).fill(0);
      for (let j = 0; j < p.length; j++) { q[j] ^= p[j]; q[j + 1] ^= gmul(p[j], EXP[i]); }
      p = q;
    }
    return p;
  }
  function rsEc(data, n) {
    const gen = rsGen(n), res = new Array(n).fill(0);
    for (const d of data) {
      const f = d ^ res[0];
      res.shift(); res.push(0);
      if (f) for (let i = 0; i < n; i++) res[i] ^= gmul(gen[i + 1], f);
    }
    return res;
  }
  const dataCap = v => EC[v][1].reduce((s, [n, k]) => s + n * k, 0);
  function bch(v, poly, bits) {          // 把 v 往左移後除以 poly 的餘數
    const hb = x => 31 - Math.clz32(x);
    let r = v << bits;
    while (hb(r) >= hb(poly)) r ^= poly << (hb(r) - hb(poly));
    return (v << bits) | r;
  }

  function build(text) {
    const bytes = Array.from(new TextEncoder().encode(text));
    let ver = 0;
    for (let v = 1; v <= 10; v++) { if (bytes.length <= dataCap(v) - (v < 10 ? 2 : 3)) { ver = v; break; } }
    if (!ver) throw new Error("QR 內容太長");
    // 資料位元
    const bits = [];
    const put = (val, n) => { for (let i = n - 1; i >= 0; i--) bits.push((val >> i) & 1); };
    put(4, 4); put(bytes.length, ver < 10 ? 8 : 16); bytes.forEach(b => put(b, 8));
    const cap = dataCap(ver) * 8;
    put(0, Math.min(4, cap - bits.length));
    while (bits.length % 8) bits.push(0);
    const cw = []; for (let i = 0; i < bits.length; i += 8) cw.push(parseInt(bits.slice(i, i + 8).join(""), 2));
    for (let i = 0; cw.length < dataCap(ver); i++) cw.push(i % 2 ? 0x11 : 0xEC);
    // 分區塊與糾錯
    const [ecn, groups] = EC[ver], blocks = [];
    let pos = 0;
    for (const [n, k] of groups) for (let i = 0; i < n; i++) { const d = cw.slice(pos, pos + k); pos += k; blocks.push({d, e: rsEc(d, ecn)}); }
    const out = [], maxd = Math.max(...blocks.map(b => b.d.length));
    for (let i = 0; i < maxd; i++) blocks.forEach(b => { if (i < b.d.length) out.push(b.d[i]); });
    for (let i = 0; i < ecn; i++) blocks.forEach(b => out.push(b.e[i]));
    const stream = []; out.forEach(b => put2(stream, b));
    function put2(a, val) { for (let i = 7; i >= 0; i--) a.push((val >> i) & 1); }

    // 矩陣
    const N = 17 + 4 * ver;
    const M = Array.from({length: N}, () => new Array(N).fill(false));
    const F = Array.from({length: N}, () => new Array(N).fill(false));   // 功能區（不放資料）
    const setf = (r, c, v) => { if (r >= 0 && c >= 0 && r < N && c < N) { M[r][c] = v; F[r][c] = true; } };
    const finder = (r0, c0) => {
      for (let r = -1; r <= 7; r++) for (let c = -1; c <= 7; c++) {
        const inx = r >= 0 && r <= 6 && c >= 0 && c <= 6;
        const dark = inx && (r === 0 || r === 6 || c === 0 || c === 6 || (r >= 2 && r <= 4 && c >= 2 && c <= 4));
        setf(r0 + r, c0 + c, dark);
      }
    };
    finder(0, 0); finder(0, N - 7); finder(N - 7, 0);
    for (let i = 8; i < N - 8; i++) { setf(6, i, i % 2 === 0); setf(i, 6, i % 2 === 0); }
    const al = ALIGN[ver];
    for (const r of al) for (const c of al) {
      if ((r === 6 && c === 6) || (r === 6 && c === al[al.length - 1]) || (r === al[al.length - 1] && c === 6)) continue;
      for (let dr = -2; dr <= 2; dr++) for (let dc = -2; dc <= 2; dc++) setf(r + dr, c + dc, Math.max(Math.abs(dr), Math.abs(dc)) !== 1);
    }
    setf(N - 8, 8, true);                                              // 固定黑點
    for (let i = 0; i < 9; i++) { if (!F[8][i]) setf(8, i, false); if (!F[i][8]) setf(i, 8, false); }   // 保留格式區
    for (let i = 0; i < 8; i++) { if (!F[8][N - 1 - i]) setf(8, N - 1 - i, false); if (!F[N - 1 - i][8]) setf(N - 1 - i, 8, false); }
    if (ver >= 7) for (let i = 0; i < 6; i++) for (let j = 0; j < 3; j++) { setf(i, N - 11 + j, false); setf(N - 11 + j, i, false); }
    // 放資料（由右下往上、兩欄一組之字形）
    const data = M.map(r => r.slice());
    let bi = 0, up = true;
    for (let c = N - 1; c > 0; c -= 2) {
      if (c === 6) c--;
      for (let k = 0; k < N; k++) {
        const r = up ? N - 1 - k : k;
        for (let dc = 0; dc < 2; dc++) {
          const cc = c - dc;
          if (F[r][cc]) continue;
          data[r][cc] = bi < stream.length ? stream[bi] === 1 : false; bi++;
        }
      }
      up = !up;
    }
    const maskFn = [(r, c) => (r + c) % 2 === 0, (r) => r % 2 === 0, (r, c) => c % 3 === 0, (r, c) => (r + c) % 3 === 0,
      (r, c) => (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0, (r, c) => (r * c) % 2 + (r * c) % 3 === 0,
      (r, c) => ((r * c) % 2 + (r * c) % 3) % 2 === 0, (r, c) => (((r + c) % 2) + ((r * c) % 3)) % 2 === 0];
    function withMask(k) {
      const m = data.map(r => r.slice());
      for (let r = 0; r < N; r++) for (let c = 0; c < N; c++) if (!F[r][c] && maskFn[k](r, c)) m[r][c] = !m[r][c];
      // 格式資訊（等級 M = 00）
      const fmt = bch((0 << 3) | k, 0x537, 10) ^ 0x5412;
      const fb = i => ((fmt >> i) & 1) === 1;
      for (let i = 0; i <= 5; i++) m[i][8] = fb(i);
      m[7][8] = fb(6); m[8][8] = fb(7); m[8][7] = fb(8);
      for (let i = 9; i < 15; i++) m[8][14 - i] = fb(i);
      for (let i = 0; i < 8; i++) m[8][N - 1 - i] = fb(i);
      for (let i = 8; i < 15; i++) m[N - 15 + i][8] = fb(i);
      m[N - 8][8] = true;
      if (ver >= 7) {
        const vi = bch(ver, 0x1F25, 12);
        for (let i = 0; i < 18; i++) { const b = ((vi >> i) & 1) === 1; m[Math.floor(i / 3)][N - 11 + (i % 3)] = b; m[N - 11 + (i % 3)][Math.floor(i / 3)] = b; }
      }
      return m;
    }
    function penalty(m) {
      let p = 0;
      for (let t = 0; t < 2; t++) {
        for (let a = 0; a < N; a++) {
          let run = 1;
          for (let b = 1; b < N; b++) {
            const cur = t ? m[b][a] : m[a][b], prv = t ? m[b - 1][a] : m[a][b - 1];
            if (cur === prv) { run++; if (run === 5) p += 3; else if (run > 5) p++; } else run = 1;
          }
        }
      }
      for (let r = 0; r < N - 1; r++) for (let c = 0; c < N - 1; c++) if (m[r][c] === m[r][c + 1] && m[r][c] === m[r + 1][c] && m[r][c] === m[r + 1][c + 1]) p += 3;
      const pat = [true, false, true, true, true, false, true, false, false, false, false], rev = pat.slice().reverse();
      const match = (get, i, q) => q.every((v, j) => get(i + j) === v);
      for (let a = 0; a < N; a++) for (let b = 0; b <= N - 11; b++) {
        for (const q of [pat, rev]) { if (match(j => m[a][j], b, q)) p += 40; if (match(j => m[j][a], b, q)) p += 40; }
      }
      let dark = 0; m.forEach(r => r.forEach(v => { if (v) dark++; }));
      p += Math.floor(Math.abs(dark * 100 / (N * N) - 50) / 5) * 10;
      return p;
    }
    let best = null, bp = Infinity;
    for (let k = 0; k < 8; k++) { const m = withMask(k), p = penalty(m); if (p < bp) { bp = p; best = m; } }
    return best;
  }
  g.qrMatrix = build;
  // 畫到 canvas（含四格安靜區）；scale 為每個模組的像素數
  g.qrDraw = function (canvas, text, scale) {
    const m = build(text), n = m.length, q = 4, s = scale || 6, size = (n + q * 2) * s;
    canvas.width = canvas.height = size;
    const x = canvas.getContext("2d");
    x.fillStyle = "#fff"; x.fillRect(0, 0, size, size); x.fillStyle = "#000";
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (m[r][c]) x.fillRect((c + q) * s, (r + q) * s, s, s);
  };
})(typeof window !== "undefined" ? window : globalThis);
