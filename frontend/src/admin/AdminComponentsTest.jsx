
import AdminLogin from "./AdminLogin.jsx";
import AdminDashboard from "./AdminDashboard.jsx";

export default function AdminComponentsTest() {
  return (
    <div>
      <AdminLogin onLoginSuccess={() => {}} onBack={() => {}} />
      <AdminDashboard
        session={{ username: "test-admin", access_token: "test-token" }}
        onLogout={() => {}}
        onBack={() => {}}
      />
    </div>
  );
}