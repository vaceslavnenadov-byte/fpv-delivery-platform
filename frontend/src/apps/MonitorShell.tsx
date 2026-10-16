import { useEffect } from "react";
import { FlightMonitorPage } from "../pages/FlightMonitor";
import { realtime } from "../realtime";

// Монитор полёта — отдельное окно без бокового меню; обновления реального времени здесь свои.
export function MonitorShell() {
  useEffect(() => {
    realtime.start();
    return () => realtime.stop();
  }, []);
  return <FlightMonitorPage />;
}
