/*
 * fm_nav.dll — Minimal, safe Unity main-thread player profile opener for FMMT 27.
 *
 * Uses ONLY Unity IL2CPP's official reflection/invocation C API exported by GameAssembly.dll
 * (zero code patching, zero Denuvo/game_plugin.dll writes, zero database mutation).
 *
 * Marshals every IL2CPP call onto FM27's Unity main thread via a WM_APP message on
 * fm.exe's main window, wrapped in Win32 SEH (__try / __except) so an error can never crash FM27.
 *
 * IPC: Named pipe \\.\pipe\FMMT27Nav_<pid>
 *   Request:  "OPEN <db_id>\n"  (or "PING\n")
 *   Response: "OK <detail>\n" or "ERR <reason>\n"
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WM_FMMT_OPEN (WM_APP + 0x270)

typedef void* (*fn_domain_get)(void);
typedef void** (*fn_domain_get_assemblies)(void* domain, size_t* size);
typedef void* (*fn_assembly_get_image)(void* assembly);
typedef size_t (*fn_image_get_class_count)(void* image);
typedef void* (*fn_image_get_class)(void* image, size_t index);
typedef void* (*fn_class_from_name)(void* image, const char* ns, const char* name);
typedef const char* (*fn_class_get_name)(void* klass);
typedef const char* (*fn_class_get_namespace)(void* klass);
typedef void* (*fn_class_get_type)(void* klass);
typedef void* (*fn_type_get_object)(void* type);
typedef void* (*fn_class_get_method_from_name)(void* klass, const char* name, int args_count);
typedef void* (*fn_class_get_methods)(void* klass, void** iter);
typedef const char* (*fn_method_get_name)(void* method);
typedef unsigned int (*fn_method_get_param_count)(void* method);
typedef void* (*fn_method_get_param)(void* method, unsigned int index);
typedef char* (*fn_type_get_name)(void* type);
typedef void* (*fn_class_get_field_from_name)(void* klass, const char* name);
typedef void (*fn_field_get_value)(void* obj, void* field, void* value);
typedef void (*fn_field_static_get_value)(void* field, void* value);
typedef void* (*fn_object_new)(void* klass);
typedef void* (*fn_object_get_virtual_method)(void* obj, void* method);
typedef void (*fn_format_exception)(void* ex, char* message, int message_size);
typedef void* (*fn_runtime_invoke)(void* method, void* obj, void** params, void** exc);
typedef void* (*fn_thread_attach)(void* domain);
typedef void (*fn_thread_detach)(void* thread);
typedef void (*fn_free)(void* ptr);
typedef void* (*fn_array_get_data)(void* arr);

typedef struct {
    HMODULE ga;
    fn_domain_get domain_get;
    fn_domain_get_assemblies domain_get_assemblies;
    fn_assembly_get_image assembly_get_image;
    fn_image_get_class_count image_get_class_count;
    fn_image_get_class image_get_class;
    fn_class_from_name class_from_name;
    fn_class_get_name class_get_name;
    fn_class_get_namespace class_get_namespace;
    fn_class_get_type class_get_type;
    fn_type_get_object type_get_object;
    fn_class_get_method_from_name class_get_method_from_name;
    fn_class_get_methods class_get_methods;
    fn_method_get_name method_get_name;
    fn_method_get_param_count method_get_param_count;
    fn_method_get_param method_get_param;
    fn_type_get_name type_get_name;
    fn_class_get_field_from_name class_get_field_from_name;
    fn_field_get_value field_get_value;
    fn_field_static_get_value field_static_get_value;
    fn_object_new object_new;
    fn_object_get_virtual_method object_get_virtual_method;
    fn_format_exception format_exception;
    fn_runtime_invoke runtime_invoke;
    fn_thread_attach thread_attach;
    fn_thread_detach thread_detach;
    fn_free il2cpp_free;
} Il2Cpp;

static Il2Cpp G;
static HWND g_hwnd = NULL;
static WNDPROC g_orig_wndproc = NULL;
static int g_inited = 0;

/* Cached IL2CPP classes & methods (resolved lazily on main thread) */
static void* g_k_person_ref = NULL;
static void* g_m_person_ref_ctor = NULL;
static void* g_k_typed_value = NULL;
static void* g_m_tv_get_ref = NULL;
static void* g_m_tv_set_val = NULL;
static void* g_k_edh = NULL;
static void* g_f_edh_person_handler = NULL;
static void* g_k_prch = NULL;
static void* g_f_prch_panel_ref = NULL;
static void* g_f_prch_param_name = NULL;
static void* g_k_panel_mgr = NULL;
static void* g_m_pm_open6 = NULL;
static void* g_k_resources = NULL;
static void* g_m_res_find_all = NULL;

typedef struct {
    int db_id;
    int ok;
    char msg[256];
    HANDLE done;
} OpenReq;

static int init_il2cpp_api(void) {
    if (g_inited) return 1;
    HMODULE ga = GetModuleHandleW(L"GameAssembly.dll");
    if (!ga) return 0;
    G.ga = ga;
    #define LOAD(f, sym) do { G.f = (fn_##f)GetProcAddress(ga, sym); if (!G.f) return 0; } while (0)
    LOAD(domain_get, "il2cpp_domain_get");
    LOAD(domain_get_assemblies, "il2cpp_domain_get_assemblies");
    LOAD(assembly_get_image, "il2cpp_assembly_get_image");
    LOAD(image_get_class_count, "il2cpp_image_get_class_count");
    LOAD(image_get_class, "il2cpp_image_get_class");
    LOAD(class_from_name, "il2cpp_class_from_name");
    LOAD(class_get_name, "il2cpp_class_get_name");
    LOAD(class_get_namespace, "il2cpp_class_get_namespace");
    LOAD(class_get_type, "il2cpp_class_get_type");
    LOAD(type_get_object, "il2cpp_type_get_object");
    LOAD(class_get_method_from_name, "il2cpp_class_get_method_from_name");
    LOAD(class_get_methods, "il2cpp_class_get_methods");
    LOAD(method_get_name, "il2cpp_method_get_name");
    LOAD(method_get_param_count, "il2cpp_method_get_param_count");
    LOAD(method_get_param, "il2cpp_method_get_param");
    LOAD(type_get_name, "il2cpp_type_get_name");
    LOAD(class_get_field_from_name, "il2cpp_class_get_field_from_name");
    LOAD(field_get_value, "il2cpp_field_get_value");
    LOAD(field_static_get_value, "il2cpp_field_static_get_value");
    LOAD(object_new, "il2cpp_object_new");
    LOAD(object_get_virtual_method, "il2cpp_object_get_virtual_method");
    LOAD(runtime_invoke, "il2cpp_runtime_invoke");
    LOAD(thread_attach, "il2cpp_thread_attach");
    LOAD(thread_detach, "il2cpp_thread_detach");
    G.format_exception = (fn_format_exception)GetProcAddress(ga, "il2cpp_format_exception");
    G.il2cpp_free = (fn_free)GetProcAddress(ga, "il2cpp_free");
    #undef LOAD
    g_inited = 1;
    return 1;
}

static void* find_class_by_name(const char* want_ns, const char* want_name) {
    void* dom = G.domain_get();
    if (!dom) return NULL;
    size_t n_asm = 0;
    void** asms = G.domain_get_assemblies(dom, &n_asm);
    for (size_t a = 0; a < n_asm; a++) {
        void* img = G.assembly_get_image(asms[a]);
        if (!img) continue;
        if (want_ns) {
            void* k = G.class_from_name(img, want_ns, want_name);
            if (k) return k;
        } else {
            size_t nc = G.image_get_class_count(img);
            for (size_t c = 0; c < nc; c++) {
                void* k = G.image_get_class(img, c);
                if (!k) continue;
                const char* nm = G.class_get_name(k);
                if (nm && strcmp(nm, want_name) == 0) return k;
            }
        }
    }
    return NULL;
}

static void* find_method_sig(void* klass, const char* name, int param_count, const char* p0_substr) {
    void* iter = NULL;
    void* m;
    while ((m = G.class_get_methods(klass, &iter)) != NULL) {
        const char* mn = G.method_get_name(m);
        if (!mn || strcmp(mn, name) != 0) continue;
        if ((int)G.method_get_param_count(m) != param_count) continue;
        if (p0_substr && param_count > 0) {
            char* pt = G.type_get_name(G.method_get_param(m, 0));
            int match = (pt && strstr(pt, p0_substr) != NULL);
            if (pt && G.il2cpp_free) G.il2cpp_free(pt);
            if (!match) continue;
        }
        return m;
    }
    return NULL;
}

static int resolve_symbols(char* err, size_t err_sz) {
    if (g_m_pm_open6) return 1;
    if (!init_il2cpp_api()) {
        snprintf(err, err_sz, "GameAssembly IL2CPP API unavailable");
        return 0;
    }
    g_k_person_ref = find_class_by_name("FM.UI", "PersonReference");
    g_k_typed_value = find_class_by_name("SI.Core", "TypedValue");
    g_k_edh = find_class_by_name("FM.UI", "EmbeddedDataHandler");
    g_k_prch = find_class_by_name(NULL, "PersonReferenceClickedHandler");
    g_k_panel_mgr = find_class_by_name("SI.Bindable", "PanelManager");
    g_k_resources = find_class_by_name("UnityEngine", "Resources");

    if (!g_k_person_ref || !g_k_typed_value || !g_k_edh || !g_k_prch || !g_k_panel_mgr || !g_k_resources) {
        snprintf(err, err_sz, "Missing class: pr=%p tv=%p edh=%p prch=%p pm=%p res=%p",
                 g_k_person_ref, g_k_typed_value, g_k_edh, g_k_prch, g_k_panel_mgr, g_k_resources);
        return 0;
    }

    g_m_person_ref_ctor = find_method_sig(g_k_person_ref, ".ctor", 1, "Int32");
    g_m_tv_get_ref = find_method_sig(g_k_typed_value, "GetReferenceTypedValue", 0, NULL);
    g_m_tv_set_val = find_method_sig(g_k_typed_value, "SetValue", 1, "System.Object");
    g_f_edh_person_handler = G.class_get_field_from_name(g_k_edh, "m_personReferenceClickedHandler");
    g_f_prch_panel_ref = G.class_get_field_from_name(g_k_prch, "m_playerPanelRef");
    g_f_prch_param_name = G.class_get_field_from_name(g_k_prch, "m_playerParamName");
    g_m_pm_open6 = find_method_sig(g_k_panel_mgr, "Open", 6, "AssetReferenceT");
    g_m_res_find_all = find_method_sig(g_k_resources, "FindObjectsOfTypeAll", 1, "System.Type");

    if (!g_m_person_ref_ctor || !g_m_tv_get_ref || !g_m_tv_set_val ||
        !g_f_edh_person_handler || !g_f_prch_panel_ref || !g_f_prch_param_name ||
        !g_m_pm_open6 || !g_m_res_find_all) {
        snprintf(err, err_sz, "Missing member: ctor=%p getref=%p setv=%p fh=%p fpr=%p fpn=%p open=%p find=%p",
                 g_m_person_ref_ctor, g_m_tv_get_ref, g_m_tv_set_val,
                 g_f_edh_person_handler, g_f_prch_panel_ref, g_f_prch_param_name,
                 g_m_pm_open6, g_m_res_find_all);
        return 0;
    }
    return 1;
}

static void* find_first_unity_object(void* klass) {
    void* ty = G.class_get_type(klass);
    void* sys_type = G.type_get_object(ty);
    if (!sys_type) return NULL;
    void* exc = NULL;
    void* args[1] = { sys_type };
    void* arr = G.runtime_invoke(g_m_res_find_all, NULL, args, &exc);
    if (exc || !arr) return NULL;
    /* IL2CPP Il2CppArray layout on x64: +0x00 klass, +0x08 monitor, +0x10 bounds, +0x18 max_length (size_t), +0x20 items[] */
    size_t len = *(size_t*)((char*)arr + 0x18);
    if (len == 0) return NULL;
    void** items = (void**)((char*)arr + 0x20);
    return items[0];
}

static void execute_open_on_main_thread(OpenReq* req) {
    __try {
        if (!resolve_symbols(req->msg, sizeof(req->msg))) {
            req->ok = 0;
            return;
        }
        void* edh = find_first_unity_object(g_k_edh);
        if (!edh) {
            snprintf(req->msg, sizeof(req->msg), "EmbeddedDataHandler instance not found");
            req->ok = 0;
            return;
        }
        void* pm = find_first_unity_object(g_k_panel_mgr);
        if (!pm) {
            snprintf(req->msg, sizeof(req->msg), "PanelManager instance not found");
            req->ok = 0;
            return;
        }
        void* prch = NULL;
        G.field_get_value(edh, g_f_edh_person_handler, &prch);
        if (!prch) {
            snprintf(req->msg, sizeof(req->msg), "m_personReferenceClickedHandler is null");
            req->ok = 0;
            return;
        }
        void* panel_ref = NULL;
        void* param_name = NULL;
        G.field_get_value(prch, g_f_prch_panel_ref, &panel_ref);
        G.field_get_value(prch, g_f_prch_param_name, &param_name);
        if (!panel_ref || !param_name) {
            snprintf(req->msg, sizeof(req->msg), "panel_ref=%p param_name=%p", panel_ref, param_name);
            req->ok = 0;
            return;
        }

        /* 1. new FM.UI.PersonReference(db_id) */
        void* person_ref = G.object_new(g_k_person_ref);
        if (!person_ref) {
            snprintf(req->msg, sizeof(req->msg), "object_new(PersonReference) failed");
            req->ok = 0;
            return;
        }
        void* exc = NULL;
        int id_val = req->db_id;
        void* ctor_args[1] = { &id_val };
        G.runtime_invoke(g_m_person_ref_ctor, person_ref, ctor_args, &exc);
        if (exc) {
            snprintf(req->msg, sizeof(req->msg), "PersonReference.ctor threw exception");
            req->ok = 0;
            return;
        }

        /* 2. tv = TypedValue.GetReferenceTypedValue(); tv.SetValue(person_ref) */
        void* tv = G.runtime_invoke(g_m_tv_get_ref, NULL, NULL, &exc);
        if (exc || !tv) {
            char em[180] = {0};
            if (exc && G.format_exception) G.format_exception(exc, em, sizeof(em));
            snprintf(req->msg, sizeof(req->msg), "GetReferenceTypedValue failed: %s", em);
            req->ok = 0;
            return;
        }
        void* set_m = G.object_get_virtual_method(tv, g_m_tv_set_val);
        void* set_args[1] = { person_ref };
        G.runtime_invoke(set_m ? set_m : g_m_tv_set_val, tv, set_args, &exc);
        if (exc) {
            char em[180] = {0};
            if (G.format_exception) G.format_exception(exc, em, sizeof(em));
            snprintf(req->msg, sizeof(req->msg), "TypedValue.SetValue exc: %s", em);
            req->ok = 0;
            return;
        }

        /* 3. PanelManager.Open(panel_ref, param_name, tv, null, null, 0) */
        int rebind = 0;
        void* open_args[6] = { panel_ref, param_name, tv, NULL, NULL, &rebind };
        G.runtime_invoke(g_m_pm_open6, pm, open_args, &exc);
        if (exc) {
            char em[180] = {0};
            if (G.format_exception) G.format_exception(exc, em, sizeof(em));
            snprintf(req->msg, sizeof(req->msg), "PanelManager.Open exc: %s", em);
            req->ok = 0;
            return;
        }

        req->ok = 1;
        snprintf(req->msg, sizeof(req->msg), "opened db_id=%d", req->db_id);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        req->ok = 0;
        snprintf(req->msg, sizeof(req->msg), "SEH 0x%08X", GetExceptionCode());
    }
}

static LRESULT CALLBACK hook_wndproc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    if (msg == WM_FMMT_OPEN && lParam != 0) {
        OpenReq* req = (OpenReq*)lParam;
        execute_open_on_main_thread(req);
        if (req->done) SetEvent(req->done);
        return 0;
    }
    return CallWindowProcW(g_orig_wndproc, hwnd, msg, wParam, lParam);
}

static BOOL CALLBACK enum_my_wnd(HWND hwnd, LPARAM lParam) {
    DWORD pid = 0;
    GetWindowThreadProcessId(hwnd, &pid);
    if (pid == GetCurrentProcessId() && IsWindowVisible(hwnd) && !GetWindow(hwnd, GW_OWNER)) {
        wchar_t cls[64] = {0};
        GetClassNameW(hwnd, cls, 64);
        if (wcscmp(cls, L"UnityWndClass") == 0 || g_hwnd == NULL) {
            g_hwnd = hwnd;
            if (wcscmp(cls, L"UnityWndClass") == 0) return FALSE;
        }
    }
    return TRUE;
}

static int ensure_wnd_hook(void) {
    if (g_orig_wndproc && g_hwnd && IsWindow(g_hwnd)) return 1;
    g_hwnd = NULL;
    EnumWindows(enum_my_wnd, 0);
    if (!g_hwnd) return 0;
    g_orig_wndproc = (WNDPROC)SetWindowLongPtrW(g_hwnd, GWLP_WNDPROC, (LONG_PTR)hook_wndproc);
    return g_orig_wndproc != NULL;
}

static DWORD WINAPI pipe_server_thread(LPVOID param) {
    wchar_t pipe_name[128];
    swprintf_s(pipe_name, 128, L"\\\\.\\pipe\\FMMT27Nav2_%lu", GetCurrentProcessId());

    for (;;) {
        HANDLE hPipe = CreateNamedPipeW(
            pipe_name,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            4, 1024, 1024, 0, NULL);
        if (hPipe == INVALID_HANDLE_VALUE) {
            Sleep(500);
            continue;
        }
        BOOL connected = ConnectNamedPipe(hPipe, NULL) ? TRUE : (GetLastError() == ERROR_PIPE_CONNECTED);
        if (connected) {
            char buf[256] = {0};
            DWORD got = 0;
            if (ReadFile(hPipe, buf, sizeof(buf) - 1, &got, NULL) && got > 0) {
                char resp[320] = {0};
                if (strncmp(buf, "PING", 4) == 0) {
                    int hooked = ensure_wnd_hook();
                    snprintf(resp, sizeof(resp), "OK PONG hooked=%d hwnd=%p\n", hooked, (void*)g_hwnd);
                } else if (strncmp(buf, "OPEN ", 5) == 0) {
                    int db_id = atoi(buf + 5);
                    if (!ensure_wnd_hook()) {
                        snprintf(resp, sizeof(resp), "ERR Unity window not found\n");
                    } else {
                        OpenReq req;
                        memset(&req, 0, sizeof(req));
                        req.db_id = db_id;
                        req.done = CreateEventW(NULL, TRUE, FALSE, NULL);
                        if (PostMessageW(g_hwnd, WM_FMMT_OPEN, 0, (LPARAM)&req)) {
                            DWORD w = WaitForSingleObject(req.done, 4000);
                            if (w == WAIT_OBJECT_0) {
                                snprintf(resp, sizeof(resp), "%s %s\n", req.ok ? "OK" : "ERR", req.msg);
                            } else {
                                snprintf(resp, sizeof(resp), "ERR timeout waiting for Unity main thread\n");
                            }
                        } else {
                            snprintf(resp, sizeof(resp), "ERR PostMessage failed\n");
                        }
                        if (req.done) CloseHandle(req.done);
                    }
                } else {
                    snprintf(resp, sizeof(resp), "ERR unknown command\n");
                }
                DWORD written = 0;
                WriteFile(hPipe, resp, (DWORD)strlen(resp), &written, NULL);
                FlushFileBuffers(hPipe);
            }
            DisconnectNamedPipe(hPipe);
        }
        CloseHandle(hPipe);
    }
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID reserved) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hModule);
        HANDLE h = CreateThread(NULL, 0, pipe_server_thread, hModule, 0, NULL);
        if (h) CloseHandle(h);
    }
    return TRUE;
}
