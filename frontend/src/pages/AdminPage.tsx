import { useState, useEffect } from "react";
import { api } from "../api/client";
import { useAuth } from "../lib/auth";

interface UserRecord {
  id: string;
  username: string;
  role: string;
  is_active: number;
  created_at: string;
  storage_bytes: number;
  book_count: number;
  daily_translation_chars: number;
  monthly_translation_chars: number;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch {
    return iso;
  }
}

export default function AdminPage() {
  const { user } = useAuth();
  const [users, setUsers] = useState<UserRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [inviteDays, setInviteDays] = useState(7);
  const [inviteCode, setInviteCode] = useState("");
  const [inviteCopied, setInviteCopied] = useState(false);

  useEffect(() => {
    loadUsers();
  }, []);

  const loadUsers = async () => {
    setLoading(true);
    try {
      const data = await api.listUsers();
      setUsers(data.users);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateInvite = async () => {
    try {
      const data = await api.createInvite(inviteDays);
      setInviteCode(data.invite_code);
      setInviteCopied(false);
    } catch (err: any) {
      alert("创建邀请码失败: " + err.message);
    }
  };

  const copyInvite = async () => {
    try {
      await navigator.clipboard.writeText(inviteCode);
      setInviteCopied(true);
      setTimeout(() => setInviteCopied(false), 2000);
    } catch {
      // fallback
      const el = document.createElement("textarea");
      el.value = inviteCode;
      document.body.appendChild(el);
      el.select();
      document.execCommand("copy");
      document.body.removeChild(el);
      setInviteCopied(true);
      setTimeout(() => setInviteCopied(false), 2000);
    }
  };

  const handleToggleActive = async (targetId: string, currentActive: number) => {
    try {
      await api.updateUser(targetId, { is_active: !currentActive });
      await loadUsers();
    } catch (err: any) {
      alert("操作失败: " + err.message);
    }
  };

  if (!user || user.role !== "admin") {
    return <div className="text-center py-12 text-gray-400">无权限访问</div>;
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-2xl font-bold">管理面板</h1>

      {/* 创建邀请码 */}
      <div className="bg-white rounded-2xl p-6 shadow-sm border space-y-4">
        <h2 className="text-lg font-semibold">创建邀请码</h2>
        <div className="flex items-center gap-3">
          <label className="text-sm text-gray-600">有效期（天）：</label>
          <input
            type="number"
            min={1}
            max={365}
            value={inviteDays}
            onChange={(e) => setInviteDays(Number(e.target.value))}
            className="w-20 px-3 py-2 border rounded-lg text-sm"
          />
          <button
            onClick={handleCreateInvite}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700"
          >
            生成邀请码
          </button>
        </div>

        {inviteCode && (
          <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
            <code className="text-lg font-mono text-blue-700 flex-1">{inviteCode}</code>
            <button
              onClick={copyInvite}
              className="px-3 py-1 text-sm bg-white border rounded hover:bg-gray-50"
            >
              {inviteCopied ? "已复制" : "复制"}
            </button>
          </div>
        )}

        <p className="text-xs text-gray-400">
          邀请码仅在创建时显示一次，请立即复制。默认 7 天有效。
        </p>
      </div>

      {/* 用户列表 */}
      <div className="bg-white rounded-2xl shadow-sm border">
        <div className="px-6 py-4 border-b">
          <h2 className="text-lg font-semibold">用户管理</h2>
        </div>

        {loading ? (
          <div className="p-6 text-center text-gray-400">加载中...</div>
        ) : error ? (
          <div className="p-6 text-center text-red-500">{error}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-gray-500">
                  <th className="text-left px-4 py-3 font-medium">用户名</th>
                  <th className="text-left px-4 py-3 font-medium">角色</th>
                  <th className="text-left px-4 py-3 font-medium">状态</th>
                  <th className="text-right px-4 py-3 font-medium">存储</th>
                  <th className="text-right px-4 py-3 font-medium">书籍</th>
                  <th className="text-right px-4 py-3 font-medium">日翻译</th>
                  <th className="text-right px-4 py-3 font-medium">月翻译</th>
                  <th className="text-left px-4 py-3 font-medium">注册时间</th>
                  <th className="text-center px-4 py-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className="border-t hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium">{u.username}</td>
                    <td className="px-4 py-3">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${
                        u.role === "admin" ? "bg-purple-100 text-purple-700" : "bg-gray-100 text-gray-600"
                      }`}>
                        {u.role === "admin" ? "管理员" : "用户"}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${
                        u.is_active ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
                      }`}>
                        {u.is_active ? "正常" : "已禁用"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">
                      {formatBytes(u.storage_bytes)}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">
                      {u.book_count}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">
                      {u.daily_translation_chars.toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-600">
                      {u.monthly_translation_chars.toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-gray-500 text-xs">
                      {formatTime(u.created_at)}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {u.role !== "admin" && (
                        <button
                          onClick={() => handleToggleActive(u.id, u.is_active)}
                          className={`text-xs px-3 py-1 rounded-lg border ${
                            u.is_active
                              ? "text-red-500 border-red-200 hover:bg-red-50"
                              : "text-green-500 border-green-200 hover:bg-green-50"
                          }`}
                        >
                          {u.is_active ? "禁用" : "启用"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
