import { Route, Routes } from "react-router";
import { APPS } from "../apps/config";
import { MonitorShell } from "../apps/MonitorShell";
import { ShellOutlet } from "../apps/shell";
import { Alert } from "../components/ui";
import { OrderDetailPage } from "../pages/OrderDetail";
import { ZonesPage } from "../pages/admin/ZonesPage";
import { CargoTypesPage, ConsigneesPage, DroneModelsPage } from "../pages/admin/CatalogPages";
import { OrdersMapPage } from "../pages/OrdersMap";
import { RecruiterOperatorsPage, RecruiterProfilesPage } from "../pages/recruiter/RecruiterPages";
import { OrgUnitsPage } from "../pages/people/OrgUnitsPage";
import { PeoplePage } from "../pages/people/PeoplePage";
import { PersonCardPage } from "../pages/people/PersonCardPage";
import { ProfilePage } from "../pages/Profile";
import { AdminAuditPage, AdminComplaintsPage, AdminDronesPage, AdminMapsPage, AdminMonitoringPage,
  AdminOrdersPage, AdminOverviewPage, AdminScoringPage, AdminUsersPage } from "../pages/admin/AdminPages";
import { supportRoutes } from "../pages/support/routes";
import { QualificationReviewPage } from "../pages/licenses/QualificationReviewPage";
import { mountApp } from "./common";

mountApp(APPS.admin, (
  <Routes>
    <Route path="/orders/:orderId/monitor" element={<MonitorShell />} />
    <Route path="/map/window" element={<OrdersMapPage standalone />} />
    <Route element={<ShellOutlet app={APPS.admin} />}>
      <Route path="/" element={<AdminOverviewPage />} />
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
      <Route path="/profiles" element={<RecruiterProfilesPage />} />
      <Route path="/operators" element={<RecruiterOperatorsPage />} />
      <Route path="/cargo-types" element={<CargoTypesPage />} />
      <Route path="/drone-models" element={<DroneModelsPage />} />
      <Route path="/consignees" element={<ConsigneesPage />} />
      <Route path="/map" element={<OrdersMapPage />} />
      <Route path="/audit" element={<AdminAuditPage />} />
      <Route path="/monitoring" element={<AdminMonitoringPage />} />
      <Route path="/orders/:id" element={<OrderDetailPage />} />
      <Route path="/qualification" element={<QualificationReviewPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      {supportRoutes(true)}
      <Route path="*" element={<Alert tone="warn">Страница не найдена</Alert>} />
    </Route>
  </Routes>
));
