// Vermo brand tokens — mirror the web app's login-panel palette
// (auth/login_ui.py) so both surfaces read as one product.
export const colors = {
  bg: "#0c1320",
  panel: "#141d2e",
  line: "#243046",
  ink: "#f4f7fa",
  muted: "#9aa8b8",
  brand: "#16806a",
  brandBright: "#1ab585",
  positive: "#2fbf71",
  negative: "#e5484d",
};

export function euro(value: number): string {
  return `€${value.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

export function percent(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}
