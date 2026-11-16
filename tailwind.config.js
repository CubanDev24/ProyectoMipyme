module.exports = {
  content: ["./templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0f19",
        side: "#090d16",
        card: "#101625",
        inner: "#0a0d14",
        line: "#1e293b",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        display: ["Fraunces", "Georgia", "serif"],
      },
    },
  },
  safelist: [
    "bg-gradient-to-br",
    "from-slate-900",
    "via-slate-900",
    "to-slate-900/90",
    "to-amber-950/20",
    "to-rose-950/20",
  ],
  plugins: [],
}