import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router";
import { useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { Alert, Pending } from "./components/ui";
import { LoginPage } from "./pages/Login";
import { OrderDetailPage } from "./pages/OrderDetail";
import { ProfilePage } from "./pages/Profile";
import { AdminAuditPage, AdminComplaintsPage, AdminDronesPage, AdminMonitoringPage, AdminOrdersPage, AdminOverviewPage,
  AdminMapsPage, AdminReviewsPage, AdminScoringPage, AdminUsersPage } from "./pages/admin/AdminPages";
import { CoordinatorPage } from "./pages/coordinator/CoordinatorPage";
import { CustomerDronesPage } from "./pages/customer/CustomerDrones";
import { CustomerOrdersPage } from "./pages/customer/CustomerOrders";
import { NewOrderPage } from "./pages/customer/NewOrder";
import { OperatorMinePage, OperatorQueuePage, OperatorScoresPage } from "./pages/operator/OperatorPages";
import { TechnicianPage } from "./pages/technician/TechnicianPage";

/** Скрытие разделов — только удобство интерфейса; права проверяет сервер. */
function Guard({ perm, children }: { perm: string; children: ReactNode }) {
  const { can } = useAuth();
  return can(perm) ? <>{children}</> : <Alert>Раздел недоступен для вашей роли.</Alert>;
}

function Home() {
  const { me, can } = useAuth();
  if (!me) return null;
  if (!me.profile_completed) return <Navigate to="/profile" replace />;
  if (can("report.read")) return <Navigate to="/admin" replace />;
  if (can("order.queue.read")) return <Navigate to="/operator" replace />;
  if (can("order.note.write")) return <Navigate to="/coordinator" replace />;
  if (can("drone.health.write")) return <Navigate to="/technician" replace />;
  if (can("order.read_own")) return <Navigate to="/customer" replace />;
  return <Navigate to="/profile" replace />;
}

export function App() {
  const { me, loading } = useAuth();
  if (loading) return <Pending label="Проверка сессии…" />;
  if (!me) {
    return (
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/orders/:id" element={<OrderDetailPage />} />
        <Route path="/customer" element={<Guard perm="order.read_own"><CustomerOrdersPage /></Guard>} />
        <Route path="/customer/new" element={<Guard perm="order.create"><NewOrderPage /></Guard>} />
        <Route path="/customer/drones" element={<Guard perm="drone.read_own"><CustomerDronesPage /></Guard>} />
        <Route path="/operator" element={<Guard perm="order.queue.read"><OperatorQueuePage /></Guard>} />
        <Route path="/operator/mine" element={<Guard perm="order.execute"><OperatorMinePage /></Guard>} />
        <Route path="/operator/scores" element={<Guard perm="score.read_own"><OperatorScoresPage /></Guard>} />
        <Route path="/coordinator" element={<Guard perm="order.note.write"><CoordinatorPage /></Guard>} />
        <Route path="/technician" element={<Guard perm="drone.health.write"><TechnicianPage /></Guard>} />
        <Route path="/admin" element={<Guard perm="report.read"><AdminOverviewPage /></Guard>} />
        <Route path="/admin/reviews" element={<Guard perm="order.review"><AdminReviewsPage /></Guard>} />
        <Route path="/admin/orders" element={<Guard perm="order.read_all"><AdminOrdersPage /></Guard>} />
        <Route path="/admin/drones" element={<Guard perm="drone.approve"><AdminDronesPage /></Guard>} />
        <Route path="/admin/complaints" element={<Guard perm="complaint.resolve"><AdminComplaintsPage /></Guard>} />
        <Route path="/admin/users" element={<Guard perm="user.manage"><AdminUsersPage /></Guard>} />
        <Route path="/admin/scoring" element={<Guard perm="scoring.manage"><AdminScoringPage /></Guard>} />
        <Route path="/admin/maps" element={<Guard perm="map.manage"><AdminMapsPage /></Guard>} />
        <Route path="/admin/audit" element={<Guard perm="audit.read"><AdminAuditPage /></Guard>} />
        <Route path="/admin/monitoring" element={<Guard perm="system.monitor"><AdminMonitoringPage /></Guard>} />
        <Route path="*" element={<Alert tone="warn">Страница не найдена</Alert>} />
      </Routes>
    </Layout>
  );
}
