import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

// Скан документа (лицензии): изображение или PDF. Справа в карточке — превью; щелчок открывает
// увеличенное изображение поверх страницы, «Закрыть» возвращает к карточке. PDF рисуется в браузере
// (pdf.js загружается только для PDF) — встраивать фреймы политика безопасности сайта не позволяет.

type PdfDoc = { numPages: number; getPage: (n: number) => Promise<PdfPage> };
type PdfPage = {
  getViewport: (o: { scale: number }) => { width: number; height: number };
  render: (o: { canvasContext: CanvasRenderingContext2D; viewport: { width: number; height: number } }) => { promise: Promise<void> };
};

async function loadPdf(url: string): Promise<PdfDoc> {
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
  const doc = await pdfjs.getDocument({ url, withCredentials: true, isEvalSupported: false }).promise;
  return doc as unknown as PdfDoc;
}

function PdfCanvas({ doc, page, width }: { doc: PdfDoc; page: number; width: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    let alive = true;
    void doc.getPage(page).then((p) => {
      const c = ref.current;
      if (!alive || !c) return;
      const base = p.getViewport({ scale: 1 });
      const ratio = window.devicePixelRatio || 1;
      const vp = p.getViewport({ scale: (width / base.width) * ratio });
      c.width = vp.width;
      c.height = vp.height;
      c.style.width = `${width}px`;
      c.style.height = `${(vp.height / ratio)}px`;
      const ctx = c.getContext("2d");
      if (ctx) void p.render({ canvasContext: ctx, viewport: vp }).promise;
    });
    return () => { alive = false; };
  }, [doc, page, width]);
  return <canvas ref={ref} className="scan-pdf-page" />;
}

export function ScanViewer({ url, type, title }: { url: string; type: string; title: string }) {
  const [open, setOpen] = useState(false);
  const [doc, setDoc] = useState<PdfDoc | null>(null);
  const [error, setError] = useState("");
  const pdf = type === "application/pdf";
  useEffect(() => {
    if (!pdf) return;
    let alive = true;
    loadPdf(url).then((d) => alive && setDoc(d), (e) => { console.warn("PDF:", e); if (alive) setError("Не удалось показать PDF"); });
    return () => { alive = false; };
  }, [url, pdf]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  const wide = Math.min(1100, window.innerWidth - 48);
  return (
    <div className="scan-viewer">
      <button type="button" className="scan-thumb" onClick={() => setOpen(true)} title="Увеличить">
        {pdf ? (doc ? <PdfCanvas doc={doc} page={1} width={420} /> : <span className="muted">{error || "Загрузка PDF…"}</span>)
             : <img src={url} alt={title} />}
      </button>
      <span className="small muted">{pdf ? `PDF${doc ? `, страниц: ${doc.numPages}` : ""}` : "Изображение"} · щёлкните, чтобы увеличить</span>
      {open && createPortal(
        <div className="lightbox scan-lightbox" role="dialog" aria-modal="true" aria-label={title}
             onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
          <div className="scan-large">
            {pdf ? (doc ? Array.from({ length: doc.numPages }, (_, i) => <PdfCanvas key={i} doc={doc} page={i + 1} width={wide} />) : null)
                 : <img src={url} alt={title} />}
          </div>
          <button type="button" className="btn primary lightbox-close" onClick={() => setOpen(false)}>Закрыть</button>
        </div>, document.body)}
    </div>
  );
}
