/**
 * A query parameter of the current page, for lazy `useState` initialisers: `/trades?leader=3ea` opens the page
 * already filtered. Pages are static and their data loads after mount, so reading it on the client is safe.
 */
export function urlParam(name: string): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(name);
}
