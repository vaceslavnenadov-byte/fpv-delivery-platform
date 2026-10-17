import { Route, Routes } from "react-router";
import { APPS } from "../apps/config";
import { MonitorShell } from "../apps/MonitorShell";
import { ShellOutlet } from "../apps/shell";
import { Alert } from "../components/ui";
import { OrderDetailPage } from "../pages/OrderDetail";
import { ZonesPage } from "../pages/admin/ZonesPage";
import { OrgUnitsPage } from "../pages/people/OrgUnitsPage";
import { PeoplePage } from "../pages/people/PeoplePage";
import { PersonCardPage } from "../pages/people/PersonCardPage";
import { ProfilePage } from "../pages/Profile";
import { AdminAuditPage, AdminComplaintsPage, AdminDronesPage, AdminMapsPage, AdminMonitoringPage,
  AdminOrdersPage, AdminOverviewPage, AdminReviewsPage, AdminScoringPage, AdminUsersPage } from "../pages/admin/AdminPages";
import { supportRoutes } from "../pages/support/routes";
import { mountApp } from "./common";

mountApp(APPS.admin, (
  <Routes>
    <Route path="/orders/:orderId/monitor" element={<MonitorShell />} />
    <Route element={<ShellOutlet app={APPS.admin} />}>
      <Route path="/" element={<AdminOverviewPage />} />
      <Route path="/reviews" element={<AdminReviewsPage />} />
      <Route path="/orders" element={<AdminOrdersPage />} />
      <Route path="/drones" element={<AdminDronesPage />} />
      <Route path="/complaints" element={<AdminComplaintsPage />} />
      <Route path="/users" element={<AdminUsersPage />} />
      <Route path="/scoring" element={<AdminScoringPage />} />
      <Route path="/maps" element={<AdminMapsPage />} />
      <Route path="/service-zones" element={<ZonesPage />} />
      <Route path="/people" element={<PeoplePage />} />
      <Route path="/people/:id" element={<PersonCardPage />} />
      <Route path="/org-units" element={<OrgUnitsPage />} />
      <Route path="/audit" element={<AdminAuditPage />} />
      <Route path="/monitoring" element={<AdminMonitoringPage />} />
      <Route path="/orders/:id" element={<OrderDetailPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      {supportRoutes(true)}
      <Route path="*" element={<Alert tone="warn">Страница не найдена</Alert>} />
    </Route>
  </Routes>
));
