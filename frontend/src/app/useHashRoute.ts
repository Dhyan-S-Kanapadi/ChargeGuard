import { useEffect, useState } from "react";

export type Route = "overview" | "disputes" | "operations" | "ai" | "merchants" | "simulator" | "settings";

function currentRoute(): Route {
  const route = window.location.hash.replace(/^#\/?/, "").split(/[?/]/)[0];
  return (["overview", "disputes", "operations", "ai", "merchants", "simulator", "settings"] as Route[]).includes(route as Route)
    ? route as Route
    : "overview";
}

export function useHashRoute() {
  const [route, setRoute] = useState(currentRoute);
  useEffect(() => {
    const update = () => setRoute(currentRoute());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return route;
}
