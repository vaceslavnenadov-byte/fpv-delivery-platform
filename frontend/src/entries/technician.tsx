import { Route, Routes } from "react-router";
import { APPS } from "../apps/config";
import { ShellOutlet } from "../apps/shell";
import { Alert } from "../components/ui";
import { ProfilePage } from "../pages/Profile";
import { FleetOrdersPage, FleetPage, TechOrderPage } from "../pages/technician/FleetPages";
import { DroneArchivePage, TechReportPage } from "../pages/technician/ArchivePages";
import { TechnicianPage } from "../pages/technician/TechnicianPage";
import { supportRoutes } from "../pages/support/routes";
import { mountApp } from "./common";

mountApp(APPS.technician, (
  <Routes>
    <Route element={<ShellOutlet app={APPS.technician} />}>
      <Route path="/" element={<FleetPage />} />
      <Route path="/orders" element={<FleetOrdersPage />} />
      <Route path="/orders/:id" element={<TechOrderPage />} />
      <Route path="/service" element={<TechnicianPage />} />
      <Route path="/archive" element={<DroneArchivePage />} />
      <Route path="/tech-report" element={<TechReportPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      {supportRoutes()}
      <Route path="*" element={<Alert tone="warn">Страница не найдена</Alert>} />
    </Route>
  </Routes>
));
