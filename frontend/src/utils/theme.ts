/**
 * theme.ts — 主题工具函数
 */

export function getInitialTheme(): "light" | "dark" {
  try {
    const saved = localStorage.getItem("navi:theme");
    if (saved === "dark" || saved === "light") return saved;
  } catch {}
  return "light";
}

export function applyTheme(theme: "light" | "dark") {
  if (theme === "dark") {
    document.documentElement.setAttribute("data-theme", "dark");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  try { localStorage.setItem("navi:theme", theme); } catch {}
}
