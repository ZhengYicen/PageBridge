import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

export default function LoginPage() {
  const { login, register } = useAuth();
  const navigate = useNavigate();

  // 登录状态
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // 注册模式
  const [showRegister, setShowRegister] = useState(false);
  const [inviteCode, setInviteCode] = useState("");
  const [registerSuccess, setRegisterSuccess] = useState("");

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
      navigate("/");
    } catch (err: any) {
      setError(err.message || "登录失败");
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await register(username, password, inviteCode);
      setRegisterSuccess("注册成功！正在跳转...");
      setTimeout(() => navigate("/"), 1000);
    } catch (err: any) {
      setError(err.message || "注册失败");
    } finally {
      setLoading(false);
    }
  };

  const switchMode = () => {
    setShowRegister(!showRegister);
    setError("");
    setRegisterSuccess("");
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-blue-600">📖 PageBridge</h1>
          <p className="text-gray-500 mt-2">双语阅读平台</p>
        </div>

        <div className="bg-white rounded-2xl shadow-sm border p-6">
          <h2 className="text-lg font-semibold mb-4">
            {showRegister ? "邀请码注册" : "登录"}
          </h2>

          {error && (
            <div className="mb-4 p-3 bg-red-50 text-red-600 text-sm rounded-lg">
              {error}
            </div>
          )}

          {registerSuccess && (
            <div className="mb-4 p-3 bg-green-50 text-green-600 text-sm rounded-lg">
              {registerSuccess}
            </div>
          )}

          <form onSubmit={showRegister ? handleRegister : handleLogin} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                用户名
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="输入用户名"
                required
                minLength={2}
                maxLength={64}
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                密码
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="输入密码（至少 8 位）"
                required
                minLength={8}
              />
            </div>

            {showRegister && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  邀请码
                </label>
                <input
                  type="text"
                  value={inviteCode}
                  onChange={(e) => setInviteCode(e.target.value)}
                  className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="输入邀请码"
                  required
                />
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className={`w-full py-2 rounded-lg text-sm font-medium transition
                ${loading
                  ? "bg-blue-300 text-white cursor-not-allowed"
                  : "bg-blue-600 text-white hover:bg-blue-700"
                }`}
            >
              {loading ? "处理中..." : showRegister ? "注册" : "登录"}
            </button>
          </form>

          <div className="mt-4 text-center">
            <button
              onClick={switchMode}
              className="text-sm text-blue-600 hover:text-blue-800"
            >
              {showRegister ? "已有账号？去登录" : "没有账号？使用邀请码注册"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
