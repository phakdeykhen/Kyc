import { BrowserRouter } from "react-router-dom";
import { AppRoutes } from "./router";
import { I18nextProvider } from "react-i18next";
import i18n from "./i18n";
import { StaffAuthProvider } from "./auth/StaffAuth";


function App() {
  return (
    <I18nextProvider i18n={i18n}>
      <StaffAuthProvider>
        <BrowserRouter basename={__BASE_PATH__}>
          <AppRoutes />
        </BrowserRouter>
      </StaffAuthProvider>
    </I18nextProvider>
  );
}

export default App;
