# 06. Песочница, безопасность, шифрование

## 1. Зачем песочница

ИГЛА работает с реальной системой пользователя: правит файлы, запускает команды, лезет в контейнеры, может править firewall. Без изоляции одна ошибка модели или одного tool ломает рабочую машину. Поэтому:

- любой execution-tool работает в **sandbox profile**;
- любой файловый доступ ограничен `allowed_paths`;
- сеть по умолчанию отключена;
- ресурсы (CPU/RAM/timeout) лимитируются;
- AI-generated tools имеют запретный список API.

## 2. Уровни риска

```
Level 0  read-only
Level 1  создание новых файлов в workspace
Level 2  изменение существующих файлов с backup
Level 3  запуск команд (read-only effects)
Level 4  изменение сервисов / контейнеров / VM
Level 5  firewall, root, сеть, удаление, credentials
```

Авторазрешение — **только** уровни 0–2. Уровни 3–5 требуют либо явной user approval, либо отдельной policy override.

## 3. Sandbox profiles

Профиль — это набор ограничений, который Kernel применяет при запуске tool.

```yaml
sandbox_profiles:

  read_only_file_access:
    fs:
      mode: read_only
      allowed_paths: ["{workspace}", "{artifact_dir}"]
      tmp: ephemeral
    network: disabled
    capabilities: []
    cpu_limit: 1
    memory_mb: 4096
    timeout_seconds: 120

  write_workspace:
    fs:
      mode: read_write
      allowed_paths: ["{workspace}", "{artifact_dir}"]
      forbidden_paths: ["/etc", "/root", "/var/lib", "/proc", "/sys"]
      tmp: ephemeral
    network: disabled
    cpu_limit: 1
    memory_mb: 4096
    timeout_seconds: 300

  isolated_command:
    container: docker
    image: igla/sandbox:latest
    fs:
      bind:
        - source: "{workspace}"
          target: "/workspace"
          mode: ro
        - source: "{artifact_dir}"
          target: "/artifacts"
          mode: rw
    network: disabled
    capabilities: []
    cpu_limit: 2
    memory_mb: 8192
    timeout_seconds: 600

  privileged_system:
    requires_user_approval: true
    container: none
    fs:
      mode: read_write
      allowed_paths: ["{workspace}", "/declared_target"]
    network: declared_only
    cpu_limit: 4
    memory_mb: 16384
    timeout_seconds: 1200
    audit_level: full
```

Профиль выбирается из `ToolManifest.sandbox.profile` и при необходимости ужесточается через `policy.network`/`policy.allowed_paths` в `ToolInvocation`.

## 4. Реализация sandbox

Несколько backend’ов с одинаковым интерфейсом:

| Backend | Когда использовать |
|---------|---------------------|
| `bubblewrap` (`bwrap`) | Лёгкие изоляции на host’е (read-only FS, без сети) |
| `firejail` / `nsjail` | Альтернатива bwrap; для исполнения скриптов и парсеров |
| `docker` | Полная изоляция, нужен runtime-image; для кода с зависимостями и сетью |
| `qemu/kvm` (поздняя стадия) | Очень опасные операции (firewall, сетевые тесты) |
| `pyo3 sandbox_runner` (Rust) | Унифицированная обвязка с ресурсами, kill-логикой, log streaming |

Для каждого backend’а — отдельный adapter. Tool сам не знает, в чём он запущен.

## 5. Запреты для AI-generated tools

В quarantine pipeline (см. [09-tool-forge.md](09-tool-forge.md)) сгенерированный модуль проверяется static analysis на blacklist:

```
subprocess
os.system
shell=True
socket
http.client
urllib.request
requests / httpx / aiohttp
pathlib доступы вне whitelist
открытие /etc, /root, /proc, /sys
chmod / chown / sudo / setuid
docker socket
запись в registry
запись в memory
импорты network/clipboard/mic/cam
```

Если модулю реально нужен shell или сеть — он не получает их напрямую. Он вызывает kernel effect tool (`safe_command_executor`, `network_request_proxy`), который сам проходит policy.

## 6. Network policy

```
network: disabled         (default)
network: declared_only    (whitelist хостов и портов в policy)
network: full             (только при approval, аудит включён)
```

Сетевые запросы идут не через произвольный requests/httpx, а через **NetworkGateway** — kernel effect tool, который:

- проверяет whitelist;
- логирует запрос/ответ;
- применяет timeout/retry policy;
- при необходимости поднимает rate limit.

Tool, которому нужна сеть, объявляет это в манифесте и получает только handle к Gateway, а не право открыть сокет.

## 7. Filesystem policy

- `allowed_paths` — whitelist.
- `forbidden_paths` — глобальный blacklist (включает `/etc`, `/root`, `/var/lib`, `/proc`, `/sys`, `~/.ssh`, `~/.gnupg`, всё под `secrets/`).
- Любой путь нормализуется до абсолютного и проверяется на «выход за workspace» (никаких `..`-обходов).
- Любая мутация файла требует backup и read receipt (см. [03-kernel-and-policies.md](03-kernel-and-policies.md)).
- Удаление файлов — Level 5; без явного approval запрещено.

## 8. Process policy

- Все дочерние процессы с обязательным `timeout_seconds` и `working_dir`.
- Проверка `command_classified`: команда классифицируется (read/inspect/test/install/restart/destructive).
- Forbidden tokens (`rm -rf /`, `mkfs.*`, `dd if=/dev/zero of=/dev/sd*`, и т. п.) — отказ ещё до запуска.
- Live log streaming через RustProcessSupervisor; kill при превышении лимитов.

## 9. Секреты

Секреты — отдельная подсистема. LLM **никогда** не получает их в открытом виде.

Хранилище:

```
.igla/secrets/
├── store.enc        # шифрованный canonical store (age/sops/libsodium)
├── handles.json     # public handles → encrypted blob index
└── policies.yaml    # кому какие handle’ы разрешены
```

Tool, которому нужен секрет, получает в `input` не значение, а handle:

```json
{
  "input": {
    "api_key_handle": "secret:openai_api_key"
  }
}
```

Kernel перед запуском tool в sandbox инжектирует расшифрованное значение в его `os.environ` строго на время исполнения; после — стирает. Логирование секретов запрещено (фильтрация в EventStore по handle pattern’у).

## 10. Шифрование данных at-rest

Шифруются:

- секреты (`.igla/secrets/`);
- скоупы памяти, помеченные `sensitive: true` (например, `secrets:*`, `creds:*`);
- snapshots файлов, содержащие credentials/PII;
- (опционально) полный vault для пользователя с включённой сильной приватностью.

Технология:

- ключи: `age` или `libsodium`/`crypto_secretbox`;
- мастер-ключ из локального keyring (Linux secret service), либо из passphrase + Argon2;
- envelope-шифрование (master key → data key → record).

ИГЛА **не** хранит мастер-ключ в открытом виде в файлах vault’а. Без passphrase/keyring запуск ИГЛЫ возможен только в read-only режиме без доступа к зашифрованным scope’ам.

## 11. Аудит

Каждое решение sandbox/security уровня — это событие в EventStore:

```
sandbox_profile_applied     network_request_blocked    secret_handle_resolved
forbidden_token_detected    backup_created             rollback_executed
file_access_denied          policy_decision_logged
```

Аудит-лог не редактируется. Отдельный экспортёр позволяет получить «срез» событий по задаче или временному окну.

## 12. Approval flow

Когда policy возвращает `needs_approval`, runtime приостанавливает шаг и показывает пользователю:

- кратко: что собирается сделать;
- риск (Level 4/5);
- evidence, на основе которого решено это делать;
- план верификации;
- план отката;
- какие данные/секреты будут затронуты.

Approval даётся точечно — на конкретный шаг, не «на всю задачу». В Preference Memory может быть зафиксировано:

```
auto_approve_levels: [0,1,2]
auto_approve_mutations_in: ["~/Coding/llama.cpp"]
never_auto_approve: ["nftables", "systemd", "docker network", "/etc/*"]
```

Но default — никаких автоапрувов выше Level 2.

## 13. Изоляция исполнителя на хосте

Дополнительные меры:

- IGLA Runtime — отдельный systemd user-сервис с ограниченным `ProtectSystem=strict`, `ProtectHome=tmpfs` (для tools), `PrivateNetwork=yes` по умолчанию;
- внутренний RPC (Orchestrator ↔ Kernel ↔ Sandbox Adapter) — через UDS с проверкой UID;
- лимиты cgroups v2 на caller-процесс (RAM/CPU/PIDs);
- никаких lingering daemon’ов от tools — все процессы привязаны к invocation\_id, при таймауте/смерти Kernel — kill всего дерева.

## 14. Резюме

```
LLM       видит handles, не значения; видит summary, не файлы.
Tool      видит только то, что Kernel инжектировал в его envelope.
Kernel    проверяет всё: пути, команды, сеть, ресурсы, секреты, политику.
Sandbox   физически ограничивает то, что не должно случиться.
EventStore знает каждое «да» и каждое «нет» — для аудита и пост-фактум разбора.
```

Эта четырёхслойная защита делает безопасность не «надеждой на разумную модель», а архитектурным свойством системы.
