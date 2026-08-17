/// <reference types="vite/client" />

// `import.meta.env.DEV` is how the login form knows not to prefill seeded
// credentials into a bundle a stranger will load. Without this reference tsc
// does not know `import.meta.env` exists at all, and `npm run build` -- which
// type-checks first -- fails rather than silently shipping them.
