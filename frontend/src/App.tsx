import { BrowserRouter, Routes, Route, Link, useNavigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./lib/auth";
import UploadPage from "./pages/UploadPage";
import BookPage from "./pages/BookPage";
import ReaderPage from "./pages/ReaderPage";
import LoginPage from "./pages/LoginPage";
import AdminPage from "./pages/AdminPage";

/** 受保护路由：未登录重定向到 /login */
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="flex items-center justify-center min-h-screen text-gray-400">加载中...</div>;
  if (!user) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <p className="text-gray-500 mb-4">请先登录</p>
          <Link to="/login" className="text-blue-600 hover:text-blue-800">前往登录</Link>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}

function Header() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  if (!user) return null;

  return (
    <header className="bg-white border-b shadow-sm">
      <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-4">
        <Link to="/" className="text-xl font-bold text-blue-600 hover:text-blue-700 shrink-0">
          📖 PageBridge
        </Link>

        <nav className="flex items-center gap-4 ml-auto text-sm">
          <span className="text-gray-500">
            {user.username}
            {user.role === "admin" && (
              <span className="ml-1.5 text-xs bg-purple-100 text-purple-700 px-1.5 py-0.5 rounded-full">
                管理员
              </span>
            )}
          </span>

          {user.role === "admin" && (
            <Link to="/admin" className="text-gray-500 hover:text-blue-600">
              管理
            </Link>
          )}

          <button
            onClick={async () => {
              await logout();
              navigate("/login");
            }}
            className="text-sm text-gray-400 hover:text-red-500"
          >
            退出
          </button>
        </nav>
      </div>
    </header>
  );
}

function AppRoutes() {
  return (
    <div className="min-h-screen flex flex-col bg-gray-50">
      <Header />

      <main className="flex-1">
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/admin" element={
            <ProtectedRoute><AdminPage /></ProtectedRoute>
          } />
          <Route path="/" element={
            <ProtectedRoute>
              <div className="max-w-6xl mx-auto w-full px-4 py-6">
                <UploadPage />
              </div>
            </ProtectedRoute>
          } />
          <Route path="/books/:bookId" element={
            <ProtectedRoute>
              <div className="max-w-6xl mx-auto w-full px-4 py-6">
                <BookPage />
              </div>
            </ProtectedRoute>
          } />
          <Route path="/read/:bookId" element={
            <ProtectedRoute><ReaderPage /></ProtectedRoute>
          } />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
