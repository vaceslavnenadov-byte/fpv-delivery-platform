import { Route, Routes } from "react-router";
import { APPS } from "../apps/config";
import { ShellOutlet } from "../apps/shell";
import { Alert } from "../components/ui";
import { ProfilePage } from "../pages/Profile";
import { OrgUnitsPage } from "../pages/people/OrgUnitsPage";
import { PeoplePage } from "../pages/people/PeoplePage";
import { PersonCardPage } from "../pages/people/PersonCardPage";
import { RecruiterOperatorsPage, RecruiterProfilesPage } from "../pages/recruiter/RecruiterPages";
import { supportRoutes } from "../pages/support/routes";
import { QualificationReviewPage } from "../pages/licenses/QualificationReviewPage";
import { mountApp } from "./common";

mountApp(APPS.recruiter, (
  <Routes>
    <Route element={<ShellOutlet app={APPS.recruiter} />}>
      <Route path="/" element={<PeoplePage />} />
      <Route path="/people" element={<PeoplePage />} />
      <Route path="/people/:id" element={<PersonCardPage />} />
      <Route path="/org-units" element={<OrgUnitsPage />} />
      <Route path="/profiles" element={<RecruiterProfilesPage />} />
      <Route path="/operators" element={<RecruiterOperatorsPage />} />
      <Route path="/qualification" element={<QualificationReviewPage />} />
      <Route path="/profile" element={<ProfilePage />} />
      {supportRoutes()}
      <Route path="*" element={<Alert tone="warn">Страница не найдена</Alert>} />
    </Route>
  </Routes>
));
