.PHONY: check ansible-check ansible-lint terraform-check

UV := /usr/bin/uv

check: ansible-check ansible-lint terraform-check

ansible-check:
	cd ansible && $(UV) run --with ansible -- ansible-playbook --syntax-check site.yml

ansible-lint:
	$(UV) run --with ansible-lint -- ansible-lint ansible/site.yml

terraform-check:
	@if [ -f terraform/versions.tf ]; then \
	  cd terraform && terraform fmt -check -recursive && terraform validate ; \
	else \
	  echo "(no terraform/versions.tf yet — skipping)" ; \
	fi
