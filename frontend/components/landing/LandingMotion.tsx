"use client";

import { useEffect } from "react";

/* Scroll motion for the landing page, loaded after it is interactive.

   - Lenis smooths wheel scrolling and drives GSAP's ScrollTrigger.
   - [data-reveal] rises into place once; [data-stagger] does so child by child.
   - [data-parallax="n"] drifts n% of its height across its section.
   - .mw-loop-section pins on wide screens while its four steps play through.
   - [data-tilt] cards lean toward the pointer and carry a spotlight.

   Nothing is hidden before this runs, so the page reads fine without it, and
   prefers-reduced-motion keeps everything still. */
export default function LandingMotion() {
  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
    let cancelled = false;
    let teardown = () => {};

    // ---- pointer: tilt + spotlight, delegated so cards need no wiring
    let tiltEl: HTMLElement | null = null;
    function onPointerMove(event: PointerEvent) {
      const target = (event.target as HTMLElement | null)?.closest<HTMLElement>("[data-tilt], [data-magnetic]");
      if (tiltEl && tiltEl !== target) resetTilt(tiltEl);
      tiltEl = target ?? null;
      if (!target) return;
      const rect = target.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      if (target.hasAttribute("data-magnetic")) {
        target.style.translate = `${((x / rect.width) - 0.5) * 10}px ${((y / rect.height) - 0.5) * 8}px`;
        return;
      }
      target.style.setProperty("--mx", `${x}px`);
      target.style.setProperty("--my", `${y}px`);
      if (!reduced) {
        target.style.setProperty("--rx", `${(0.5 - y / rect.height) * 5}deg`);
        target.style.setProperty("--ry", `${(x / rect.width - 0.5) * 6}deg`);
      }
    }
    function resetTilt(el: HTMLElement) {
      el.style.removeProperty("--rx");
      el.style.removeProperty("--ry");
      el.style.translate = "";
    }
    if (finePointer) document.addEventListener("pointermove", onPointerMove, { passive: true });

    const nav = document.querySelector<HTMLElement>(".mw-nav");
    const bar = document.querySelector<HTMLElement>(".mw-progress");

    (async () => {
      const [{ gsap }, { ScrollTrigger }, { default: Lenis }] = await Promise.all([
        import("gsap"),
        import("gsap/ScrollTrigger"),
        import("lenis"),
      ]);
      if (cancelled) return;
      gsap.registerPlugin(ScrollTrigger);

      let lenis: InstanceType<typeof Lenis> | null = null;
      const raf = (time: number) => lenis?.raf(time * 1000);
      if (!reduced) {
        lenis = new Lenis({ lerp: 0.11, smoothWheel: true, anchors: { offset: -72 } });
        lenis.on("scroll", ScrollTrigger.update);
        gsap.ticker.add(raf);
        gsap.ticker.lagSmoothing(0);
      }

      const mm = gsap.matchMedia();
      const ctx = gsap.context(() => {
        // nav: solid after the top, hides while reading down, returns on the way up
        ScrollTrigger.create({
          start: 0,
          end: "max",
          onUpdate(self) {
            const y = self.scroll();
            nav?.classList.toggle("is-scrolled", y > 24);
            nav?.classList.toggle("is-hidden", self.direction === 1 && y > 520);
            if (bar) bar.style.transform = `scaleX(${self.progress.toFixed(4)})`;
          },
        });

        if (reduced) return;

        gsap.utils.toArray<HTMLElement>("[data-reveal]").forEach((el) => {
          gsap.from(el, {
            y: 46,
            opacity: 0,
            duration: 1.15,
            ease: "expo.out",
            clearProps: "transform,opacity",
            scrollTrigger: { trigger: el, start: "top 88%", once: true },
          });
        });

        gsap.utils.toArray<HTMLElement>("[data-stagger]").forEach((group) => {
          gsap.from(group.children, {
            y: 40,
            opacity: 0,
            duration: 1,
            ease: "expo.out",
            stagger: 0.09,
            clearProps: "transform,opacity", // hand transform back to the CSS tilt
            scrollTrigger: { trigger: group, start: "top 86%", once: true },
          });
        });

        gsap.utils.toArray<HTMLElement>("[data-parallax]").forEach((el) => {
          gsap.to(el, {
            yPercent: Number(el.dataset.parallax) || -10,
            ease: "none",
            scrollTrigger: { trigger: el.closest("section") ?? el, start: "top bottom", end: "bottom top", scrub: true },
          });
        });

        // hero copy eases back as the 3D logo takes the scroll
        gsap.to(".mw-hero-copy", {
          y: -70,
          opacity: 0.15,
          ease: "none",
          scrollTrigger: { trigger: ".mw-hero", start: "top top", end: "bottom top", scrub: true },
        });

        // the loop: pinned on wide screens, one step per quarter of the scroll
        mm.add("(min-width: 980px)", () => {
          const section = document.querySelector<HTMLElement>(".mw-loop-section");
          if (!section) return;
          // every element of a step (list item, ring node, code) carries data-step
          const steps = Array.from(section.querySelectorAll<HTMLElement>("[data-step]"));
          const count = new Set(steps.map((step) => step.dataset.step)).size;
          section.classList.add("is-pinned");
          const setStep = (progress: number) => {
            const current = Math.min(count - 1, Math.floor(progress * count));
            section.style.setProperty("--p", progress.toFixed(4));
            steps.forEach((step) => {
              const i = Number(step.dataset.step);
              step.classList.toggle("is-active", i === current);
              step.classList.toggle("is-done", i < current);
            });
          };
          setStep(0);
          ScrollTrigger.create({
            trigger: section,
            start: "top top",
            end: "+=200%",
            pin: ".mw-loop-pin",
            scrub: 0.5,
            onUpdate: (self) => setStep(self.progress),
          });
          return () => {
            section.classList.remove("is-pinned");
            section.style.removeProperty("--p");
          };
        });
      });

      // 3D canvases and fonts can shift layout after load; measure again
      const refresh = () => ScrollTrigger.refresh();
      window.addEventListener("load", refresh);
      document.fonts?.ready.then(refresh).catch(() => undefined);

      teardown = () => {
        window.removeEventListener("load", refresh);
        mm.revert();
        ctx.revert();
        gsap.ticker.remove(raf);
        lenis?.destroy();
      };
    })();

    return () => {
      cancelled = true;
      document.removeEventListener("pointermove", onPointerMove);
      teardown();
    };
  }, []);

  return null;
}
