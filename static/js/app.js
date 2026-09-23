/* 技术就绪度与制造成熟度评价系统 — 交互脚本（弹窗 / Toast / 筛选 / 防误操作） */
(function () {
  "use strict";

  /* ---------- Modal ---------- */
  function openModal(id) {
    var backdrop = document.getElementById(id);
    if (!backdrop) return;
    backdrop.classList.add("open");
    document.body.style.overflow = "hidden";
    var focusable = backdrop.querySelector("input, textarea, select, button:not(.modal-close)");
    if (focusable) setTimeout(function () { focusable.focus(); }, 60);
  }

  /* 弹窗内有未保存的修改时，关闭前确认 */
  function isDirty(backdrop) {
    return Array.prototype.some.call(backdrop.querySelectorAll("form[data-dirty-guard]"), function (form) {
      return form.getAttribute("data-dirty") === "1";
    });
  }

  function closeModal(backdrop, force) {
    if (!force && isDirty(backdrop) && !window.confirm("有未保存的修改，确定关闭吗？")) return;
    backdrop.querySelectorAll("form[data-dirty-guard]").forEach(function (form) {
      if (form.getAttribute("data-dirty") === "1") { form.reset(); form.removeAttribute("data-dirty"); }
    });
    backdrop.classList.remove("open");
    if (!document.querySelector(".modal-backdrop.open")) {
      document.body.style.overflow = "";
    }
  }

  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-modal-open]");
    if (opener) {
      event.preventDefault();
      openModal(opener.getAttribute("data-modal-open"));
      return;
    }
    var closer = event.target.closest("[data-modal-close]");
    if (closer) {
      event.preventDefault();
      var backdrop = closer.closest(".modal-backdrop");
      if (backdrop) closeModal(backdrop);
      return;
    }
    if (event.target.classList && event.target.classList.contains("modal-backdrop")) {
      closeModal(event.target);
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      var open = document.querySelector(".modal-backdrop.open");
      if (open) closeModal(open);
    }
  });

  /* ---------- Toast 自动消退 ---------- */
  document.querySelectorAll(".toast").forEach(function (toast) {
    var timer = setTimeout(function () {
      toast.style.transition = "opacity .2s ease-out";
      toast.style.opacity = "0";
      setTimeout(function () { toast.remove(); }, 220);
    }, 6000);
    var close = toast.querySelector(".close");
    if (close) {
      close.addEventListener("click", function () {
        clearTimeout(timer);
        toast.remove();
      });
    }
  });

  /* ---------- 判定弹窗内：结论切换提示文案 ---------- */
  document.querySelectorAll("[data-eval-form]").forEach(function (form) {
    var hints = {
      satisfied: "自评为满足：请结合佐证材料说明该条件如何被满足。",
      not_satisfied: "自评为不满足：请说明差距与不满足情况。",
      not_applicable: "自评为不适用：请说明不适用理由。"
    };
    var hintEl = form.querySelector("[data-statement-hint]");
    form.querySelectorAll('input[name="result"]').forEach(function (radio) {
      radio.addEventListener("change", function () {
        if (hintEl && hints[radio.value]) hintEl.textContent = hints[radio.value];
      });
    });
  });

  /* ---------- 细则库筛选 ---------- */
  var ruleFilter = document.getElementById("rule-filter");
  var trackFilter = document.getElementById("rule-track-filter");
  function applyRuleFilter() {
    var keyword = (ruleFilter && ruleFilter.value || "").trim().toLowerCase();
    var track = (trackFilter && trackFilter.value) || "";
    document.querySelectorAll("[data-rule-row]").forEach(function (row) {
      var text = row.getAttribute("data-text") || "";
      var rowTrack = row.getAttribute("data-track") || "";
      var visible = (!keyword || text.indexOf(keyword) !== -1) && (!track || rowTrack === track);
      row.style.display = visible ? "" : "none";
    });
    document.querySelectorAll("details.rule-level").forEach(function (level) {
      var anyVisible = Array.prototype.some.call(
        level.querySelectorAll("[data-rule-row]"),
        function (row) { return row.style.display !== "none"; }
      );
      level.style.display = anyVisible ? "" : "none";
      if (keyword || track) level.open = anyVisible;
    });
  }
  if (ruleFilter) ruleFilter.addEventListener("input", applyRuleFilter);
  if (trackFilter) trackFilter.addEventListener("change", applyRuleFilter);

  /* ---------- 表单修改标记 + 提交防重复 ---------- */
  document.querySelectorAll("form[data-dirty-guard]").forEach(function (form) {
    form.addEventListener("input", function () { form.setAttribute("data-dirty", "1"); });
    form.addEventListener("change", function () { form.setAttribute("data-dirty", "1"); });
    form.addEventListener("submit", function () { form.removeAttribute("data-dirty"); });
  });
  document.querySelectorAll("form[method='post']").forEach(function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector("button[type='submit']");
      if (button) setTimeout(function () { button.disabled = true; }, 0);
    });
  });

  /* ---------- 注册页：刷新图形验证码 ---------- */
  function refreshCaptcha() {
    var img = document.querySelector("[data-captcha]");
    if (img) img.src = img.src.split("?")[0] + "?t=" + Date.now();
  }
  document.querySelectorAll("[data-captcha], [data-captcha-refresh]").forEach(function (el) {
    el.addEventListener("click", refreshCaptcha);
  });

  /* ---------- 申报页：勾选评价内容后才显示目标等级 ---------- */
  document.querySelectorAll("[data-toggle-target]").forEach(function (box) {
    var target = document.getElementById(box.getAttribute("data-toggle-target"));
    function sync() { if (target) target.classList.toggle("hidden", !box.checked); }
    box.addEventListener("change", sync);
    sync();
  });

  /* ---------- 页面加载后自动打开指定弹窗（佐证上传回跳） ---------- */
  var auto = document.body.getAttribute("data-auto-modal");
  if (auto) {
    setTimeout(function () { openModal(auto); }, 120);
  }

  window.TRL = { openModal: openModal };
})();
