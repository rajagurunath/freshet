/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "rgb(var(--bg) / <alpha-value>)",
        "bg-elevated": "rgb(var(--bg-elevated) / <alpha-value>)",
        "bg-sunken": "rgb(var(--bg-sunken) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)",
        "ink-soft": "rgb(var(--ink-soft) / <alpha-value>)",
        "ink-faint": "rgb(var(--ink-faint) / <alpha-value>)",
        border: "rgb(var(--border) / <alpha-value>)",
        "border-strong": "rgb(var(--border-strong) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        "accent-ink": "rgb(var(--accent-ink) / <alpha-value>)",
        "accent-wash": "rgb(var(--accent-wash) / <alpha-value>)",
        success: "rgb(var(--success) / <alpha-value>)",
        warn: "rgb(var(--warn) / <alpha-value>)",
        danger: "rgb(var(--danger) / <alpha-value>)",
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        micro: ["12px", { lineHeight: "16px" }],
        small: ["13px", { lineHeight: "18px" }],
        body: ["15px", { lineHeight: "23px" }],
        h3: ["16px", { lineHeight: "22px", letterSpacing: "-0.01em" }],
        h2: ["20px", { lineHeight: "26px", letterSpacing: "-0.015em" }],
        h1: ["28px", { lineHeight: "34px", letterSpacing: "-0.02em" }],
        display: ["40px", { lineHeight: "44px", letterSpacing: "-0.025em" }],
      },
      borderRadius: { card: "10px" },
      maxWidth: { content: "1180px", read: "720px" },
      transitionTimingFunction: { "out-soft": "cubic-bezier(0.22, 1, 0.36, 1)" },
    },
  },
  plugins: [],
};
