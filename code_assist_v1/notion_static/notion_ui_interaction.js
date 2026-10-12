(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionUiInteraction = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const placedPanels = new Map();
  let watchingPlacement = false, placementFrame = 0, panelObserver = null;

  function forgetPanel(panel) {
    placedPanels.delete(panel);
    panelObserver?.unobserve(panel);
  }

  function refreshPlacements() {
    if (placementFrame) return;
    placementFrame = requestAnimationFrame(() => {
      placementFrame = 0;
      for (const [panel, entry] of [...placedPanels]) {
        if (!panel.isConnected || panel.hidden || !panel.getClientRects().length) {forgetPanel(panel);continue;}
        if (entry.anchor.isConnected === false) {forgetPanel(panel);continue;}
        place(panel, entry.anchor, entry.side);
      }
    });
  }

  function range(ids, anchor, focus) {
    const end = ids.indexOf(focus);
    if (end < 0) return [];
    const start = Math.max(0, ids.includes(anchor) ? ids.indexOf(anchor) : end);
    return ids.slice(Math.min(start, end), Math.max(start, end) + 1);
  }
  function step(ids, focus, delta) {
    if (!ids.length) return null;
    return ids[Math.max(0, Math.min(ids.length - 1, Math.max(0, ids.indexOf(focus)) + delta))];
  }
  function intersecting(rects, from, to) {
    const left = Math.min(from.x, to.x), right = Math.max(from.x, to.x);
    const top = Math.min(from.y, to.y), bottom = Math.max(from.y, to.y);
    return rects.filter(rect => rect.right >= left && rect.left <= right && rect.bottom >= top && rect.top <= bottom).map(rect => rect.id);
  }
  function selectionRectangle(from, to, clip) {
    const left = Math.max(clip.left, Math.min(from.x, to.x));
    const top = Math.max(clip.top, Math.min(from.y, to.y));
    const right = Math.min(clip.right, Math.max(from.x, to.x));
    const bottom = Math.min(clip.bottom, Math.max(from.y, to.y));
    return {left, top, width: Math.max(0, right - left), height: Math.max(0, bottom - top)};
  }
  function placement(anchor, size, viewport, side = "below", margin = 8, gap = 4) {
    const edge = side === "pointer" ? 12 : margin;
    const width = Math.min(size.width, Math.max(0, viewport.width - edge * 2));
    const height = Math.min(size.height, Math.max(0, viewport.height - edge * 2));
    const below = viewport.height - anchor.bottom - gap - edge;
    const above = anchor.top - gap - edge;
    let left = anchor.left, top;
    if (side === "center-below") {
      left = anchor.left + (anchor.right - anchor.left - width) / 2;
    }
    if (side === "pointer") {
      left = anchor.left;
      top = anchor.top;
    } else if (side === "row") {
      left = anchor.left + 8;
      top = anchor.top + 9;
    } else if (side === "left-center") {
      left = anchor.left - gap - width >= margin ? anchor.left - gap - width : anchor.right + gap;
      top = (anchor.top + anchor.bottom - height) / 2;
    } else if (side === "right") {
      left = anchor.right + gap + width <= viewport.width - margin ? anchor.right + gap : anchor.left - gap - width;
      top = anchor.top;
    } else {
      const useAbove = side === "above" ? above >= height || above > below : below < height && above > below;
      top = useAbove ? anchor.top - height - gap : anchor.bottom + gap;
    }
    return {left: Math.max(edge, Math.min(left, viewport.width - width - edge)), top: Math.max(edge, Math.min(top, viewport.height - height - edge)), width, height};
  }
  function place(panel, anchor, side = "below") {
    if (!panel || !anchor) return;
    const firstPlacement = !placedPanels.has(panel);
    placedPanels.set(panel, {anchor, side});
    for (const candidate of placedPanels.keys()) if (candidate.isConnected === false) forgetPanel(candidate);
    if (!watchingPlacement) {
      window.addEventListener("resize", refreshPlacements);
      window.addEventListener("scroll", refreshPlacements, true);
      watchingPlacement = true;
    }
    if (firstPlacement && typeof ResizeObserver === "function") {
      panelObserver ||= new ResizeObserver(refreshPlacements);
      panelObserver.observe(panel);
    }
    const rect = typeof anchor.getBoundingClientRect === "function" ? anchor.getBoundingClientRect() : anchor;
    panel.style.position = "fixed";
    panel.style.bottom = "auto";
    panel.style.maxWidth = side === "pointer" ? "calc(100vw - 24px)" : "calc(100vw - 16px)";
    // The slash picker sits 13px below the slash glyph and keeps 82px clear at
    // the viewport bottom when there is enough room for a useful result list.
    const slashMenu = panel.id === "blockMenu" && panel.dataset.mode === "slash";
    const gap = slashMenu ? 13 : side === "left-center" ? 5 : 4;
    const bottomReserve = slashMenu ? 82 : 12;
    const belowHeight = window.innerHeight - rect.bottom - gap - bottomReserve;
    const availableHeight = side === "right" || side === "left-center" || side === "row" || side === "pointer" ? window.innerHeight - (side === "pointer" ? 24 : 16)
      : slashMenu && belowHeight >= 240 ? belowHeight : Math.max(belowHeight, rect.top - 12);
    panel.style.maxHeight = `min(var(--popover-max-height, 430px), ${Math.max(0, Math.min(window.innerHeight - 16, availableHeight))}px)`;
    const size = panel.getBoundingClientRect();
    const value = placement(rect, size, {width: window.innerWidth, height: window.innerHeight}, side, 8, gap);
    panel.style.left = `${value.left}px`;
    panel.style.top = `${value.top}px`;
    if (side === "below" || side === "above") {
      panel.dataset.anchorPlacement = value.top >= rect.bottom ? "below"
        : value.top + value.height <= rect.top ? "above" : "overlap";
    }
  }

  function bindGutterSelection(host) {
    let drag = null, frame = 0, suppressClickUntil = 0, marquee = null, disposed = false;
    let resizeObserver = null;
    const observed = new Set();
    const listeners = [];
    const listen = (target, type, listener, options) => {
      target.addEventListener(type, listener, options);
      listeners.push(() => target.removeEventListener?.(type, listener, options));
    };
    const rowId = host.rowId || (row => row.dataset.blockId);
    const rowContent = host.rowContent || (row => row.querySelector('.block-main') || row);
    const rows = () => host.rows().filter(row => !row.hidden && row.getClientRects().length);
    const requestPaint = () => { if (drag?.moving && !frame) frame = requestAnimationFrame(paint); };
    function watchLayout(elements) {
      if (typeof ResizeObserver !== 'function') return;
      if (!resizeObserver) {
        const currentDrag = drag;
        resizeObserver = new ResizeObserver(() => {
          if (drag === currentDrag) requestPaint();
        });
      }
      const current = new Set([drag.canvas, drag.surface, ...elements]);
      for (const element of observed) if (!current.has(element)) {
        resizeObserver.unobserve(element); observed.delete(element);
      }
      for (const element of current) if (!observed.has(element)) {
        resizeObserver.observe(element); observed.add(element);
      }
    }
    function finish(cancelled = false, completed = false) {
      if (!drag) return;
      const ending = drag;
      drag = null;
      resizeObserver?.disconnect(); resizeObserver = null; observed.clear();
      const wasMoving = ending.moving;
      if (cancelled && host.pageKey() === ending.pageKey) {
        const available = new Set(rows().map(rowId));
        host.select(ending.previous.filter(id => available.has(id)), {gridRows: ending.previousGridRows});
      }
      try { ending.canvas.releasePointerCapture(ending.pointerId); } catch (_) { /* Navigation may remove the canvas. */ }
      cancelAnimationFrame(frame); frame = 0;
      marquee?.remove(); marquee = null;
      document.body.classList.remove("block-selecting");
      if (wasMoving) suppressClickUntil = performance.now() + 100;
      host.finished?.({cancelled, completed, moving: wasMoving, additive: ending.additive, pageKey: ending.pageKey});
    }
    function paint(allowScroll = true) {
      frame = 0;
      if (!drag || host.pageKey() !== drag.pageKey || !drag.canvas.isConnected || drag.canvas.hidden) return finish();
      if (!drag.moving) return;
      const scroller = drag.scroller;
      const view = scroller === document.scrollingElement ? {top: 0, bottom: window.innerHeight} : scroller.getBoundingClientRect();
      const surfaceRect = drag.surface.getBoundingClientRect();
      const toolbarBottom = host.toolbarBottom?.() ?? drag.surface.querySelector?.(".topbar")?.getBoundingClientRect().bottom ?? 0;
      const clip = {
        left: Math.max(0, surfaceRect.left), right: Math.min(window.innerWidth, surfaceRect.right),
        top: Math.max(0, surfaceRect.top, view.top, toolbarBottom), bottom: Math.min(window.innerHeight, surfaceRect.bottom, view.bottom),
      };
      const edge = Math.min(48, Math.max(0, (clip.bottom - clip.top) / 2));
      const speed = !edge ? 0 : drag.y < clip.top + edge ? -Math.min(18, (clip.top + edge - drag.y) / 3) : drag.y > clip.bottom - edge ? Math.min(18, (drag.y - clip.bottom + edge) / 3) : 0;
      const previousScrollTop = scroller.scrollTop;
      if (allowScroll !== false && speed) scroller.scrollTop += speed;
      const scrolled = scroller.scrollTop !== previousScrollTop;
      const offset = scroller.scrollTop - drag.scrollTop;
      const from = {x: drag.startX, y: drag.startY - offset};
      const to = {x: drag.x, y: drag.y};
      // The document column excludes both gutters; keep their drag feedback visible.
      const rectangle = selectionRectangle(from, to, clip);
      if (!marquee) {
        marquee = document.createElement("div");
        marquee.className = ['block-selection-marquee', host.marqueeClass || ''].filter(Boolean).join(' ');
        marquee.setAttribute("aria-hidden", "true");
        document.body.appendChild(marquee);
      }
      Object.assign(marquee.style, {
        left: `${rectangle.left}px`, top: `${rectangle.top}px`,
        width: `${rectangle.width}px`, height: `${rectangle.height}px`,
      });
      const clamp = (value, low, high) => Math.max(low, Math.min(value, high));
      // Keep the document-space start while auto-scrolling, so rows already
      // crossed remain selected after they leave the visible marquee.
      const visibleFrom = {x: clamp(from.x, clip.left, clip.right),
        y: offset ? from.y : clamp(from.y, clip.top, clip.bottom)};
      const visibleTo = {x: clamp(to.x, clip.left, clip.right), y: clamp(to.y, clip.top, clip.bottom)};
      const currentRows = rows();
      const contents = currentRows.map(rowContent);
      watchLayout(contents);
      const rects = currentRows.map((row, index) => ({...rectValue(contents[index]), id: rowId(row)}));
      const hitIds = rectangle.width && rectangle.height ? intersecting(rects, visibleFrom, visibleTo) : [];
      const refined = host.refineSelection?.({rows: currentRows, ids: hitIds, from: visibleFrom, to: visibleTo});
      const selected = refined?.ids || hitIds;
      const gridRows = refined?.gridRows || [];
      const selectedGridRows = drag.additive
        ? [...new Map([...drag.previousGridRows, ...gridRows].map(entry => [entry.grid, entry])).values()]
        : gridRows;
      if (drag.additive) for (const entry of selectedGridRows) {
        const previous = drag.previousGridRows.find(item => item.grid === entry.grid)?.rows || [];
        entry.rows = [...new Set([...previous, ...entry.rows])];
      }
      const available = new Set(rects.map(rect => rect.id));
      const previous = drag.previous.filter(id => available.has(id));
      const forwards = from.y <= to.y;
      host.select(drag.additive ? [...new Set([...previous, ...selected])] : selected, {
        anchorId: selected[forwards ? 0 : selected.length - 1],
        focusId: selected[forwards ? selected.length - 1 : 0],
        gridRows: selectedGridRows,
      });
      // A clamped edge needs no more frames; pointer/scroll events resume painting.
      if (scrolled) frame = requestAnimationFrame(paint);
    }
    function rectValue(element) {
      const r = element.getBoundingClientRect();
      return {left: r.left, right: r.right, top: r.top, bottom: r.bottom};
    }
    listen(document, "pointerdown", event => {
      if (disposed || drag || event.defaultPrevented || event.button !== 0 || event.isPrimary === false || event.pointerType === "touch") return;
      const target = event.target;
      const custom = host.start ? host.start(event) : null;
      if (host.start && !custom) return;
      const canvas = custom?.canvas || document.querySelector("#pageView");
      const surface = custom?.surface || target.closest?.("#workspaceMain");
      if (!surface || !canvas || canvas.hidden || !host.pageKey()) return;
      if (!host.start && target.closest(".block-main,button,a,input,textarea,select,[contenteditable],[role='grid'],[role='dialog'],[role='menu'],.side-peek,.topbar,#workspaceLandingBanner,.page-head,.page-cover,.page-outline")) return;
      // Page margins above/below the body and empty column gaps are selection surfaces.
      // Only body rows are selected; crossing the page title never selects the title.
      // The target exclusions above leave text, controls and cell selection intact.
      let scroller = custom?.scroller || canvas;
      while (!custom?.scroller && scroller !== document.documentElement && !(scroller.scrollHeight > scroller.clientHeight && /auto|scroll/.test(getComputedStyle(scroller).overflowY))) scroller = scroller.parentElement;
      if (scroller === document.documentElement) scroller = document.scrollingElement;
      event.preventDefault();
      drag = {canvas, surface, pointerId: event.pointerId, pageKey: host.pageKey(), scroller, scrollTop: scroller.scrollTop, startX: event.clientX, startY: event.clientY, x: event.clientX, y: event.clientY, previous: host.selected(), previousGridRows: host.selectedGridRows?.() || [], additive: event.shiftKey, moving: false};
      canvas.setPointerCapture(event.pointerId);
    });
    listen(document, "pointermove", event => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      drag.x = event.clientX; drag.y = event.clientY;
      if (!drag.moving && Math.hypot(drag.x - drag.startX, drag.y - drag.startY) < 4) return;
      drag.moving = true;
      document.activeElement?.blur?.();
      window.getSelection()?.removeAllRanges();
      document.body.classList.add("block-selecting");
      event.preventDefault();
      if (!frame) frame = requestAnimationFrame(paint);
    });
    listen(document, "pointerup", event => {
      if (drag && event.pointerId === drag.pointerId) {
        drag.x = event.clientX; drag.y = event.clientY;
        cancelAnimationFrame(frame); frame = 0;
        if (drag.moving) { event.preventDefault(); paint(false); }
        finish(false, true);
      }
    });
    listen(document, "pointercancel", event => {
      if (drag && event.pointerId === drag.pointerId) finish(true);
    });
    listen(document, "lostpointercapture", event => {
      if (drag && event.pointerId === drag.pointerId) finish(true);
    });
    listen(window, "scroll", () => {
      if (drag?.moving && !frame) frame = requestAnimationFrame(paint);
    }, true);
    listen(window, "resize", () => finish(true));
    listen(window, "blur", () => finish(true));
    listen(document, "visibilitychange", () => { if (document.hidden) finish(true); });
    const unsubscribe = host.subscribe?.(event => {
      // A stationary pointer has no animation frame pending. Document events
      // must clean up or redraw without waiting for another mouse movement.
      if (event.type === "document-loaded" || event.type === "document-unloaded") finish();
      else if (event.type === "remote-projection-applied" && drag?.moving && !frame) frame = requestAnimationFrame(paint);
    });
    listen(document, "keydown", event => {
      if (drag && event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); finish(true); }
    }, true);
    listen(document, "click", event => {
      if (performance.now() < suppressClickUntil) { event.preventDefault(); event.stopImmediatePropagation(); suppressClickUntil = 0; }
    }, true);
    return {
      active: () => Boolean(drag),
      cancel: () => finish(true),
      refresh: () => { if (drag?.moving && !frame) frame = requestAnimationFrame(paint); },
      dispose: () => { disposed = true; finish(); listeners.splice(0).forEach(remove => remove()); unsubscribe?.(); suppressClickUntil = 0; },
    };
  }
  function bindBlockMove(host) {
    let drag=null,frame=0,suppressUntil=0,disposed=false;
    const cleanup=[];
    const listen=(target,type,fn,options)=>{target.addEventListener(type,fn,options);cleanup.push(()=>target.removeEventListener(type,fn,options));};
    function finish(cancelled=false){
      if(!drag)return;const ending=drag;drag=null;cancelAnimationFrame(frame);frame=0;host.preview(null);
      try{ending.handle.releasePointerCapture(ending.pointerId);}catch{}
      if(ending.moving)suppressUntil=performance.now()+100;
      if(cancelled)host.select(ending.previous);
      else if(ending.moving&&ending.destination&&host.enabled()&&host.signature()===ending.signature)host.move(ending.ids,ending.destination);
    }
    function paint(allowScroll=true){
      frame=0;if(!drag)return;
      if(!host.enabled()||host.signature()!==drag.signature||!drag.handle.isConnected){finish(true);return;}
      if(!drag.moving)return;
      const scroller=host.scroller,rect=scroller.getBoundingClientRect(),previous=scroller.scrollTop;
      const inColumn=drag.x>=host.surface.getBoundingClientRect().left&&drag.x<=host.surface.getBoundingClientRect().right;
      const speed=!inColumn?0:drag.y<rect.top+48?-Math.min(18,(rect.top+48-drag.y)/3):drag.y>rect.bottom-48?Math.min(18,(drag.y-rect.bottom+48)/3):0;
      if(allowScroll&&speed)scroller.scrollTop+=speed;
      drag.destination=host.destination({x:drag.x,y:drag.y},drag.ids);host.preview(drag.destination);
      if(scroller.scrollTop!==previous)frame=requestAnimationFrame(paint);
    }
    listen(host.surface,'pointerdown',event=>{
      if(disposed||drag||event.defaultPrevented||event.button!==0||event.isPrimary===false||!host.enabled())return;
      const handle=host.handle(event.target);if(!handle)return;
      event.preventDefault();const previous=host.selected(),id=host.id(handle);
      if(!previous.includes(id))host.select([id]);
      drag={handle,pointerId:event.pointerId,previous,ids:host.selected(),signature:host.signature(),startX:event.clientX,startY:event.clientY,x:event.clientX,y:event.clientY,moving:false,destination:null};
      handle.setPointerCapture(event.pointerId);
    });
    listen(document,'pointermove',event=>{
      if(!drag||drag.pointerId!==event.pointerId)return;
      drag.x=event.clientX;drag.y=event.clientY;
      if(!drag.moving&&Math.hypot(drag.x-drag.startX,drag.y-drag.startY)<4)return;
      if(!drag.moving){drag.moving=true;host.started?.();document.activeElement?.blur?.();window.getSelection()?.removeAllRanges();}
      event.preventDefault();if(!frame)frame=requestAnimationFrame(paint);
    });
    listen(document,'pointerup',event=>{
      if(!drag||drag.pointerId!==event.pointerId)return;
      drag.x=event.clientX;drag.y=event.clientY;cancelAnimationFrame(frame);frame=0;
      if(drag.moving){event.preventDefault();paint(false);}finish();
    });
    for(const type of ['pointercancel','lostpointercapture'])listen(document,type,event=>{if(drag?.pointerId===event.pointerId)finish(true);});
    listen(document,'keydown',event=>{if(drag&&event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();finish(true);}},true);
    listen(host.surface,'click',event=>{if(performance.now()<suppressUntil){event.preventDefault();event.stopImmediatePropagation();suppressUntil=0;}},true);
    listen(window,'blur',()=>finish(true));listen(window,'resize',()=>finish(true));
    listen(window,'scroll',()=>{if(drag?.moving&&!frame)frame=requestAnimationFrame(paint);},true);
    listen(document,'visibilitychange',()=>{if(document.hidden)finish(true);});
    return {active:()=>Boolean(drag),cancel:()=>finish(true),dispose:()=>{disposed=true;finish(true);cleanup.splice(0).forEach(fn=>fn());}};
  }
  return {range, step, intersecting, selectionRectangle, placement, place, releasePlacement: forgetPanel, bindGutterSelection, bindBlockMove};
});
