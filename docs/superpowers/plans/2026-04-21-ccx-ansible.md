# ccx — Ansible Playbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Idempotent `ansible-pull` playbook that turns a blank Debian 12 arm64 instance into a fully-configured coding station (zsh + p10k, asdf, docker, claude-code, aws-cli, rtk, dotfiles + claude-config, verified).

**Architecture:** Role-per-concern. `site.yml` applies ten roles in dependency order. The playbook runs against `localhost` via `ansible-pull` (cloud-init pulls the repo and executes the playbook locally on the instance). Every role is re-runnable; `ansible-playbook --syntax-check` and `ansible-lint` are the laptop-side smoke tests.

The `dotfiles` role fetches a read-only deploy key for `dsylla/claude-config` from SSM (`/ccx/claude_config_deploy_key`), clones that repo, and symlinks `~/.claude/{CLAUDE.md,skills}` into it. Other `~/.claude/*` paths (settings.json, RTK.md, commands/, hooks/) are symlinked from `sesio__ccx/dotfiles/.claude/`. This means the server picks up new skills via `git pull` in `~/claude-config/` — no re-copy into this repo.

**Tech Stack:** Ansible 2.15+, Debian 12 bookworm arm64, root escalation via sudo, `aws` CLI (for SSM fetch).

**Prereqs:**
- `ccx-dotfiles` plan applied (the `dotfiles/` tree exists in this repo).
- `ccx-terraform-main` plan has provisioned the SSM SecureString `/ccx/claude_config_deploy_key`, and the user has seeded it with the private half of a deploy key whose public half is registered on `github.com/dsylla/claude-config/settings/keys`. (See the terraform-main plan for the setup recipe.)

---

## File Structure

```
sesio__ccx/
├── ansible/
│   ├── ansible.cfg
│   ├── site.yml
│   ├── inventory
│   ├── group_vars/
│   │   └── all.yml
│   └── roles/
│       ├── base/tasks/main.yml
│       ├── user/tasks/main.yml
│       ├── user/handlers/main.yml
│       ├── aws_cli/tasks/main.yml        # runs before dotfiles (needed for SSM fetch)
│       ├── zsh/tasks/main.yml
│       ├── dotfiles/tasks/main.yml
│       ├── asdf/tasks/main.yml
│       ├── docker/tasks/main.yml
│       ├── claude_code/tasks/main.yml
│       ├── rtk/tasks/main.yml            # install rtk CLI (token-saving proxy)
│       └── verify/tasks/main.yml
├── .ansible-lint
└── Makefile
```

---

### Task 1: Scaffold config, inventory, site.yml, group_vars

**Files:**
- Create: `ansible/ansible.cfg`
- Create: `ansible/inventory`
- Create: `ansible/site.yml`
- Create: `ansible/group_vars/all.yml`

- [ ] **Step 1: Directories**

```bash
mkdir -p /home/david/Work/sesio/sesio__ccx/ansible/{group_vars,roles}
for r in base user aws_cli zsh dotfiles asdf docker claude_code rtk verify; do
  mkdir -p /home/david/Work/sesio/sesio__ccx/ansible/roles/$r/tasks
done
mkdir -p /home/david/Work/sesio/sesio__ccx/ansible/roles/user/handlers
```

- [ ] **Step 2: ansible.cfg**

File `/home/david/Work/sesio/sesio__ccx/ansible/ansible.cfg`:

```ini
[defaults]
inventory           = inventory
roles_path          = roles
host_key_checking   = False
retry_files_enabled = False
stdout_callback     = yaml
forks               = 5

[privilege_escalation]
become          = True
become_method   = sudo
become_user     = root
become_ask_pass = False
```

- [ ] **Step 3: inventory**

File `/home/david/Work/sesio/sesio__ccx/ansible/inventory`:

```
localhost ansible_connection=local
```

- [ ] **Step 4: group_vars/all.yml**

File `/home/david/Work/sesio/sesio__ccx/ansible/group_vars/all.yml`:

```yaml
---
target_user: david
target_uid: 1000
target_home: /home/david
target_shell: /usr/bin/zsh
aws_region: eu-west-1
repo_url: https://github.com/dsylla/sesio__ccx.git
repo_clone_path: /home/david/sesio__ccx
authorized_ssh_key_url: https://github.com/dsylla.keys
asdf_version: v0.14.0
asdf_plugins:
  - python
  - nodejs
  - ruby
oh_my_zsh_plugins:
  - git
  - asdf
  - sudo
  - ruby
  - aws
  - shell-aws-autoprofile
zsh_theme: powerlevel10k/powerlevel10k

# claude-config (single source of truth for ~/.claude/{CLAUDE.md,skills})
claude_config_repo: git@github-claude-config:dsylla/claude-config.git
claude_config_clone_path: /home/david/claude-config
claude_config_ssm_param: /ccx/claude_config_deploy_key

# rtk (Rust Token Killer — CLI proxy for Claude Code token savings)
rtk_release_url: https://github.com/rtk-ai/rtk/releases/latest/download/rtk-aarch64-unknown-linux-gnu.tar.gz
rtk_install_dir: /usr/local/bin
```

- [ ] **Step 5: site.yml**

File `/home/david/Work/sesio/sesio__ccx/ansible/site.yml`:

```yaml
---
- name: Provision ccx coding station
  hosts: localhost
  become: true
  gather_facts: true
  roles:
    - base
    - user
    - aws_cli     # must run before dotfiles (dotfiles uses `aws ssm get-parameter`)
    - zsh
    - dotfiles
    - asdf
    - docker
    - claude_code
    - rtk
    - verify
```

- [ ] **Step 6: Syntax-check**

Run: `cd /home/david/Work/sesio/sesio__ccx/ansible && ansible-playbook --syntax-check site.yml`
Expected: `playbook: site.yml` with no errors.

---

### Task 2: Role `base`

**Files:**
- Create: `ansible/roles/base/tasks/main.yml`

- [ ] **Step 1: Write tasks**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/base/tasks/main.yml`:

```yaml
---
- name: Update apt cache
  ansible.builtin.apt:
    update_cache: yes
    cache_valid_time: 3600

- name: Install base packages
  ansible.builtin.apt:
    name:
      - build-essential
      - curl
      - git
      - jq
      - tmux
      - unzip
      - fail2ban
      - unattended-upgrades
      - ca-certificates
      - gnupg
      - lsb-release
      - python3
      - python3-pip
    state: present

- name: Configure unattended-upgrades (security only)
  ansible.builtin.copy:
    dest: /etc/apt/apt.conf.d/50unattended-upgrades
    content: |
      Unattended-Upgrade::Origins-Pattern {
        "origin=Debian,codename=${distro_codename},label=Debian-Security";
      };
      Unattended-Upgrade::Automatic-Reboot "false";
    owner: root
    group: root
    mode: "0644"

- name: Enable periodic unattended-upgrades
  ansible.builtin.copy:
    dest: /etc/apt/apt.conf.d/20auto-upgrades
    content: |
      APT::Periodic::Update-Package-Lists "1";
      APT::Periodic::Unattended-Upgrade "1";
    owner: root
    group: root
    mode: "0644"

- name: Ensure fail2ban is enabled and running
  ansible.builtin.systemd:
    name: fail2ban
    enabled: yes
    state: started
```

- [ ] **Step 2: Syntax-check**

Run: `cd /home/david/Work/sesio/sesio__ccx/ansible && ansible-playbook --syntax-check site.yml`
Expected: no errors.

---

### Task 3: Role `user`

**Files:**
- Create: `ansible/roles/user/tasks/main.yml`
- Create: `ansible/roles/user/handlers/main.yml`

- [ ] **Step 1: Write tasks**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/user/tasks/main.yml`:

```yaml
---
- name: Create target user
  ansible.builtin.user:
    name: "{{ target_user }}"
    uid: "{{ target_uid }}"
    shell: /bin/bash   # zsh role flips this after zsh is installed
    groups: sudo
    append: yes
    create_home: yes
    home: "{{ target_home }}"

- name: Passwordless sudo for target_user
  ansible.builtin.copy:
    dest: "/etc/sudoers.d/90-{{ target_user }}"
    content: "{{ target_user }} ALL=(ALL) NOPASSWD:ALL\n"
    owner: root
    group: root
    mode: "0440"
    validate: "visudo -cf %s"

- name: Ensure .ssh directory
  ansible.builtin.file:
    path: "{{ target_home }}/.ssh"
    state: directory
    owner: "{{ target_user }}"
    group: "{{ target_user }}"
    mode: "0700"

- name: Download authorized SSH keys from GitHub
  ansible.builtin.get_url:
    url: "{{ authorized_ssh_key_url }}"
    dest: "{{ target_home }}/.ssh/authorized_keys"
    owner: "{{ target_user }}"
    group: "{{ target_user }}"
    mode: "0600"
    force: yes

- name: Harden sshd (key-only, no root)
  ansible.builtin.lineinfile:
    path: /etc/ssh/sshd_config
    regexp: "{{ item.regexp }}"
    line: "{{ item.line }}"
    validate: "sshd -t -f %s"
  loop:
    - { regexp: '^#?PasswordAuthentication ',         line: 'PasswordAuthentication no' }
    - { regexp: '^#?PermitRootLogin ',                line: 'PermitRootLogin no' }
    - { regexp: '^#?ChallengeResponseAuthentication ', line: 'ChallengeResponseAuthentication no' }
  notify: Restart sshd
```

- [ ] **Step 2: Write handler**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/user/handlers/main.yml`:

```yaml
---
- name: Restart sshd
  ansible.builtin.systemd:
    name: ssh
    state: restarted
```

- [ ] **Step 3: Syntax-check**

Run: `cd /home/david/Work/sesio/sesio__ccx/ansible && ansible-playbook --syntax-check site.yml`
Expected: no errors.

---

### Task 4: Role `zsh`

**Files:**
- Create: `ansible/roles/zsh/tasks/main.yml`

- [ ] **Step 1: Write tasks**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/zsh/tasks/main.yml`:

```yaml
---
- name: Install zsh
  ansible.builtin.apt:
    name: zsh
    state: present

- name: Set zsh as default shell for target_user
  ansible.builtin.user:
    name: "{{ target_user }}"
    shell: "{{ target_shell }}"

- name: Install oh-my-zsh (unattended)
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    set -e
    if [ ! -d "{{ target_home }}/.oh-my-zsh" ]; then
      RUNZSH=no CHSH=no sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
    fi
  args:
    creates: "{{ target_home }}/.oh-my-zsh"

- name: Install powerlevel10k theme
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.git:
    repo: https://github.com/romkatv/powerlevel10k.git
    dest: "{{ target_home }}/.oh-my-zsh/custom/themes/powerlevel10k"
    depth: 1
    version: master

- name: Install zsh-autosuggestions
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.git:
    repo: https://github.com/zsh-users/zsh-autosuggestions.git
    dest: "{{ target_home }}/.oh-my-zsh/custom/plugins/zsh-autosuggestions"
    depth: 1
    version: master

- name: Install zsh-syntax-highlighting
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.git:
    repo: https://github.com/zsh-users/zsh-syntax-highlighting.git
    dest: "{{ target_home }}/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting"
    depth: 1
    version: master

- name: Install shell-aws-autoprofile (optional)
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.git:
    repo: https://github.com/dsylla/shell-aws-autoprofile.git
    dest: "{{ target_home }}/.oh-my-zsh/custom/plugins/shell-aws-autoprofile"
    depth: 1
    version: main
  ignore_errors: true
```

- [ ] **Step 2: Syntax-check**

Run: `cd /home/david/Work/sesio/sesio__ccx/ansible && ansible-playbook --syntax-check site.yml`
Expected: no errors.

---

### Task 5: Role `dotfiles`

**Files:**
- Create: `ansible/roles/dotfiles/tasks/main.yml`

- [ ] **Step 1: Write tasks**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/dotfiles/tasks/main.yml`:

```yaml
---
# --- sesio__ccx clone (public, HTTPS) -------------------------------------
- name: Ensure sesio__ccx repo is present at canonical path
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.git:
    repo: "{{ repo_url }}"
    dest: "{{ repo_clone_path }}"
    version: main
    update: yes

- name: Ensure ~/.ssh exists
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.file:
    path: "{{ target_home }}/.ssh"
    state: directory
    mode: "0700"

# --- claude-config deploy key (fetched from SSM, retrying until seeded) ---
- name: Wait for the claude-config deploy key to be seeded in SSM, then write it
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    set -e
    KEY_PATH="{{ target_home }}/.ssh/claude_config_deploy_key"

    # Up to 20 min (60 iters × 20s). Terraform apply creates the SSM param with a
    # PLACEHOLDER; the user runs `aws ssm put-parameter --overwrite ...` after apply
    # to seed the real value. This loop bridges the race.
    for i in $(seq 1 60); do
      VAL=$(aws ssm get-parameter \
              --name "{{ claude_config_ssm_param }}" \
              --with-decryption \
              --region "{{ aws_region }}" \
              --query 'Parameter.Value' \
              --output text 2>/dev/null || echo "")
      case "$VAL" in
        ""|PLACEHOLDER*)
          echo "[ccx] SSM {{ claude_config_ssm_param }} not yet seeded (try $i/60)..."
          sleep 20
          ;;
        *)
          printf '%s' "$VAL" > "$KEY_PATH"
          chmod 600 "$KEY_PATH"
          exit 0
          ;;
      esac
    done
    echo "[ccx] timeout waiting for SSM parameter to be seeded" >&2
    exit 1
  args:
    creates: "{{ target_home }}/.ssh/claude_config_deploy_key"

- name: Write SSH config alias for claude-config clone
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.blockinfile:
    path: "{{ target_home }}/.ssh/config"
    create: yes
    mode: "0600"
    marker: "# {mark} ANSIBLE MANAGED - claude-config"
    block: |
      Host github-claude-config
        HostName github.com
        User git
        IdentityFile {{ target_home }}/.ssh/claude_config_deploy_key
        IdentitiesOnly yes
        StrictHostKeyChecking accept-new

# --- claude-config clone (private, via deploy key alias) ------------------
- name: Clone claude-config
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.git:
    repo: "{{ claude_config_repo }}"
    dest: "{{ claude_config_clone_path }}"
    version: master
    update: yes

# --- symlink top-level shell/editor dotfiles into $HOME -------------------
- name: Find top-level files in dotfiles/
  ansible.builtin.find:
    paths: "{{ repo_clone_path }}/dotfiles"
    file_type: file
    hidden: yes
    excludes:
      - README.md
  register: _dotfiles_top

- name: Symlink top-level dotfiles into $HOME
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.file:
    src: "{{ item.path }}"
    dest: "{{ target_home }}/{{ item.path | basename }}"
    state: link
    force: yes
  loop: "{{ _dotfiles_top.files }}"
  loop_control:
    label: "{{ item.path | basename }}"

# --- assemble ~/.claude/ from two sources ---------------------------------
- name: Ensure .claude directory exists
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.file:
    path: "{{ target_home }}/.claude"
    state: directory
    mode: "0700"

- name: Symlink ~/.claude/* from sesio__ccx dotfiles (real files + local dirs)
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.file:
    src: "{{ repo_clone_path }}/dotfiles/.claude/{{ item }}"
    dest: "{{ target_home }}/.claude/{{ item }}"
    state: link
    force: yes
  loop:
    - settings.json
    - RTK.md
    - commands
    - hooks

- name: Symlink ~/.claude/CLAUDE.md -> claude-config/CLAUDE.md
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.file:
    src: "{{ claude_config_clone_path }}/CLAUDE.md"
    dest: "{{ target_home }}/.claude/CLAUDE.md"
    state: link
    force: yes

- name: Symlink ~/.claude/skills -> claude-config/skills
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.file:
    src: "{{ claude_config_clone_path }}/skills"
    dest: "{{ target_home }}/.claude/skills"
    state: link
    force: yes
```

- [ ] **Step 2: Syntax-check**

Run: `cd /home/david/Work/sesio/sesio__ccx/ansible && ansible-playbook --syntax-check site.yml`
Expected: no errors.

---

### Task 6: Role `asdf`

**Files:**
- Create: `ansible/roles/asdf/tasks/main.yml`

- [ ] **Step 1: Write tasks**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/asdf/tasks/main.yml`:

```yaml
---
- name: Install asdf build dependencies
  ansible.builtin.apt:
    name:
      - autoconf
      - bison
      - libssl-dev
      - libyaml-dev
      - libreadline-dev
      - zlib1g-dev
      - libncurses5-dev
      - libffi-dev
      - libgdbm-dev
      - libsqlite3-dev
      - libbz2-dev
      - liblzma-dev
      - libxml2-dev
      - libxmlsec1-dev
    state: present

- name: Clone asdf
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.git:
    repo: https://github.com/asdf-vm/asdf.git
    dest: "{{ target_home }}/.asdf"
    version: "{{ asdf_version }}"

- name: Install asdf plugins
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    source "{{ target_home }}/.asdf/asdf.sh"
    asdf plugin list | grep -qx "{{ item }}" || asdf plugin add "{{ item }}"
  args:
    executable: /bin/bash
  loop: "{{ asdf_plugins }}"
  changed_when: false

- name: Install latest stable python / nodejs / ruby (and set global)
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    source "{{ target_home }}/.asdf/asdf.sh"
    ver=$(asdf latest "{{ item }}")
    asdf list "{{ item }}" | grep -qx "  $ver" || asdf install "{{ item }}" "$ver"
    asdf global "{{ item }}" "$ver"
  args:
    executable: /bin/bash
  loop: "{{ asdf_plugins }}"
  changed_when: false
```

- [ ] **Step 2: Syntax-check**

Run: `cd /home/david/Work/sesio/sesio__ccx/ansible && ansible-playbook --syntax-check site.yml`
Expected: no errors.

---

### Task 7: Role `docker`

**Files:**
- Create: `ansible/roles/docker/tasks/main.yml`

- [ ] **Step 1: Write tasks**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/docker/tasks/main.yml`:

```yaml
---
- name: Ensure apt keyring dir
  ansible.builtin.file:
    path: /etc/apt/keyrings
    state: directory
    mode: "0755"

- name: Docker GPG key
  ansible.builtin.get_url:
    url: https://download.docker.com/linux/debian/gpg
    dest: /etc/apt/keyrings/docker.asc
    mode: "0644"

- name: Docker apt repo (arm64)
  ansible.builtin.apt_repository:
    repo: "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian {{ ansible_distribution_release }} stable"
    filename: docker
    state: present
    update_cache: yes

- name: Install Docker CE packages
  ansible.builtin.apt:
    name:
      - docker-ce
      - docker-ce-cli
      - containerd.io
      - docker-buildx-plugin
      - docker-compose-plugin
    state: present

- name: Enable + start docker
  ansible.builtin.systemd:
    name: docker
    enabled: yes
    state: started

- name: Add target_user to docker group
  ansible.builtin.user:
    name: "{{ target_user }}"
    groups: docker
    append: yes
```

- [ ] **Step 2: Syntax-check**

Run: `cd /home/david/Work/sesio/sesio__ccx/ansible && ansible-playbook --syntax-check site.yml`
Expected: no errors.

---

### Task 8: Role `claude_code`

**Files:**
- Create: `ansible/roles/claude_code/tasks/main.yml`

- [ ] **Step 1: Write tasks**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/claude_code/tasks/main.yml`:

```yaml
---
- name: Install @anthropic-ai/claude-code globally via asdf Node
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    source "{{ target_home }}/.asdf/asdf.sh"
    npm install -g @anthropic-ai/claude-code
  args:
    executable: /bin/bash
  register: _claude_install
  changed_when: "'added' in _claude_install.stdout or 'updated' in _claude_install.stdout"
```

- [ ] **Step 2: Syntax-check**

Run: `cd /home/david/Work/sesio/sesio__ccx/ansible && ansible-playbook --syntax-check site.yml`
Expected: no errors.

---

### Task 9: Role `aws_cli`

**Files:**
- Create: `ansible/roles/aws_cli/tasks/main.yml`

- [ ] **Step 1: Write tasks**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/aws_cli/tasks/main.yml`:

```yaml
---
- name: Detect installed aws cli (if any)
  ansible.builtin.command: aws --version
  register: _awscli_version
  failed_when: false
  changed_when: false

- name: Download AWS CLI v2 installer (arm64)
  ansible.builtin.unarchive:
    src: https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip
    dest: /tmp
    remote_src: yes
  when: _awscli_version.rc != 0

- name: Install AWS CLI v2
  ansible.builtin.command: /tmp/aws/install --update
  when: _awscli_version.rc != 0
  changed_when: true
```

- [ ] **Step 2: Syntax-check**

Run: `cd /home/david/Work/sesio/sesio__ccx/ansible && ansible-playbook --syntax-check site.yml`
Expected: no errors.

---

### Task 10: Role `rtk`

**Files:**
- Create: `ansible/roles/rtk/tasks/main.yml`

- [ ] **Step 1: Write tasks**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/rtk/tasks/main.yml`:

```yaml
---
- name: Detect installed rtk (if any)
  ansible.builtin.command: "{{ rtk_install_dir }}/rtk --version"
  register: _rtk_version
  failed_when: false
  changed_when: false

- name: Download rtk release tarball (arm64)
  ansible.builtin.get_url:
    url: "{{ rtk_release_url }}"
    dest: /tmp/rtk-aarch64.tar.gz
    mode: "0644"
  when: _rtk_version.rc != 0

- name: Extract rtk
  ansible.builtin.unarchive:
    src: /tmp/rtk-aarch64.tar.gz
    dest: /tmp
    remote_src: yes
    creates: /tmp/rtk
  when: _rtk_version.rc != 0

- name: Install rtk to {{ rtk_install_dir }}
  ansible.builtin.copy:
    src: /tmp/rtk
    dest: "{{ rtk_install_dir }}/rtk"
    mode: "0755"
    owner: root
    group: root
    remote_src: yes
  when: _rtk_version.rc != 0

- name: Clean up rtk extraction artifacts
  ansible.builtin.file:
    path: "{{ item }}"
    state: absent
  loop:
    - /tmp/rtk-aarch64.tar.gz
    - /tmp/rtk
  when: _rtk_version.rc != 0
```

**Notes:**
- The tarball's internal layout may place the binary at `/tmp/rtk` directly or at `/tmp/<some-subdir>/rtk`. If `--syntax-check` passes but the install step fails at runtime, inspect the tarball (`tar tzf /tmp/rtk-aarch64.tar.gz`) and adjust the `src:` of the `copy` task.
- `rtk-rewrite.sh` (shipped in the dotfiles' `.claude/hooks/`) requires `rtk >= 0.23.0` and `jq`. `jq` is installed by the `base` role. Version guard in the hook warns + exits 0 on older rtk.
- No `rtk init -g` run here — the hook is already wired via the symlinked `settings.json` + `hooks/rtk-rewrite.sh`.

- [ ] **Step 2: Syntax-check**

Run: `cd /home/david/Work/sesio/sesio__ccx/ansible && ansible-playbook --syntax-check site.yml`
Expected: no errors.

---

### Task 11: Role `verify`

**Files:**
- Create: `ansible/roles/verify/tasks/main.yml`

- [ ] **Step 1: Write tasks**

File `/home/david/Work/sesio/sesio__ccx/ansible/roles/verify/tasks/main.yml`:

```yaml
---
- name: Verify zsh is installed and is target_user's login shell
  ansible.builtin.shell: |
    set -e
    test "$(getent passwd {{ target_user }} | cut -d: -f7)" = "{{ target_shell }}"
    zsh --version
  register: _v_zsh
  changed_when: false

- name: Verify docker runs and user is in docker group
  ansible.builtin.shell: |
    set -e
    docker --version
    id -nG {{ target_user }} | tr ' ' '\n' | grep -qx docker
  register: _v_docker
  changed_when: false

- name: Verify asdf + languages
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    set -e
    source "{{ target_home }}/.asdf/asdf.sh"
    asdf --version
    python --version
    node --version
    ruby --version
  args:
    executable: /bin/bash
  register: _v_asdf
  changed_when: false

- name: Verify claude code
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    source "{{ target_home }}/.asdf/asdf.sh"
    claude --version
  args:
    executable: /bin/bash
  register: _v_claude
  changed_when: false

- name: Verify aws cli
  ansible.builtin.shell: aws --version
  register: _v_aws
  changed_when: false

- name: Verify rtk
  ansible.builtin.command: "{{ rtk_install_dir }}/rtk --version"
  register: _v_rtk
  changed_when: false

- name: Verify claude-config clone + symlinks
  become_user: "{{ target_user }}"
  become: true
  ansible.builtin.shell: |
    set -e
    test -d "{{ claude_config_clone_path }}/.git"
    test "$(readlink {{ target_home }}/.claude/CLAUDE.md)" = "{{ claude_config_clone_path }}/CLAUDE.md"
    test "$(readlink {{ target_home }}/.claude/skills)"   = "{{ claude_config_clone_path }}/skills"
    git -C "{{ claude_config_clone_path }}" rev-parse --short HEAD
  register: _v_claude_config
  changed_when: false

- name: Write provision-ok marker
  ansible.builtin.copy:
    dest: /var/log/ccx-provision-ok
    content: |
      zsh:           {{ _v_zsh.stdout | trim }}
      docker:        {{ _v_docker.stdout | trim }}
      asdf:
      {{ _v_asdf.stdout | indent(8, true) }}
      claude:        {{ _v_claude.stdout | trim }}
      aws:           {{ _v_aws.stdout | trim }}
      rtk:           {{ _v_rtk.stdout | trim }}
      claude-config: {{ _v_claude_config.stdout | trim }}
      time:          {{ ansible_date_time.iso8601 }}
    owner: root
    group: root
    mode: "0644"
```

- [ ] **Step 2: Syntax-check**

Run: `cd /home/david/Work/sesio/sesio__ccx/ansible && ansible-playbook --syntax-check site.yml`
Expected: no errors.

---

### Task 12: Lint + Makefile check target

**Files:**
- Create: `.ansible-lint`
- Create: `Makefile`

- [ ] **Step 1: `.ansible-lint`**

File `/home/david/Work/sesio/sesio__ccx/.ansible-lint`:

```yaml
---
profile: moderate
exclude_paths:
  - .cache/
  - .terraform/
skip_list:
  - yaml[line-length]
```

- [ ] **Step 2: `Makefile`**

File `/home/david/Work/sesio/sesio__ccx/Makefile`:

```makefile
.PHONY: check ansible-check ansible-lint terraform-check

check: ansible-check ansible-lint terraform-check

ansible-check:
	cd ansible && ansible-playbook --syntax-check site.yml

ansible-lint:
	ansible-lint ansible/site.yml

terraform-check:
	cd terraform/bootstrap && terraform fmt -check -recursive && terraform validate
	@if [ -f terraform/versions.tf ]; then \
	  cd terraform && terraform fmt -check -recursive && terraform validate ; \
	fi
```

- [ ] **Step 3: Run `make check`**

Run: `cd /home/david/Work/sesio/sesio__ccx && make check`
Expected: `ansible-check` passes, `ansible-lint` passes (warnings OK, errors not), `terraform-check` passes for bootstrap. Main terraform is conditionally skipped if `terraform/versions.tf` doesn't yet exist.

---

### Task 13: Commit

- [ ] **Step 1: Review**

Run: `cd /home/david/Work/sesio/sesio__ccx && git status && git diff --cached --stat`
Expected: files under `ansible/`, `.ansible-lint`, `Makefile`.

- [ ] **Step 2: Commit**

Invoke `/commit`. Suggested message: `feat(ansible): playbook with 10 roles for ccx provisioning`.

---

## Done when

1. `ansible-playbook --syntax-check ansible/site.yml` exits clean.
2. `ansible-lint ansible/site.yml` has no errors.
3. `make check` passes.
4. The `verify` role writes `/var/log/ccx-provision-ok` when the playbook runs end-to-end on a real instance (validated in the Terraform-main plan's smoke tests).
