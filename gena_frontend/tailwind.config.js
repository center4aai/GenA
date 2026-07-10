/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
      colors: {
        // Palette tuned to complement the GenA logo (a charcoal engraving on
        // warm ivory): warm neutral surfaces + a deep forest-green accent that
        // nods to the reptile mark instead of the previous cool blue/navy.
        gena: {
          primary: '#3f6b4f',
          'primary-dark': '#345a42',
          sidebar: '#20201c',
          'sidebar-hover': '#2e2c26',
          accent: '#7ba585',
          surface: '#f5f0e5',
        },
      },
      boxShadow: {
        card: '0 1px 3px 0 rgb(0 0 0 / 0.06), 0 1px 2px -1px rgb(0 0 0 / 0.06)',
      },
    },
  },
  plugins: [],
};
