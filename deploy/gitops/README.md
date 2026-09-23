# Separate platform and application GitOps packages

These OpenShift Kustomize entry points support the two-instance model introduced
by [hypershell-gitops PR #285](https://github.com/openshift-online/hypershell-gitops/pull/285):
one OpenShift GitOps operator manages a platform Argo CD instance and a separate
application Argo CD instance in each cluster.

This is a packaging change, not a live ownership migration. It does not create
Argo CD instances, grant permissions, change images, or change runtime settings.

## Ownership

| Entry point | Intended owner | Resources |
| --- | --- | --- |
| `deploy/gitops/applications` | Application Argo CD (`hypershell-gitops`) | API server and web console Deployments, their ServiceAccounts and Services, and the API Route |
| `deploy/gitops/platform` | Platform Argo CD (`openshift-gitops`) | Namespace, controller Deployment/ServiceAccount/Service, cluster RBAC and SCC bindings, monitoring, shared certificate authorities and network policies |
| `deploy/hub` | Existing single deployment owner | Compatibility composition of both packages |
| `deploy/base` | Existing dev/overlay consumers | Compatibility composition without the OpenShift Route/SCC additions |

The application package contains only namespaced resources. It does not install
operators, CRDs, cluster RBAC, namespaces, or the privileged controller. The
platform package also contains namespaced resources: ownership is based on
privilege and responsibility, not just Kubernetes resource scope.

All built-in monitoring remains together in the platform package because it
includes cluster-wide read permissions and node-exporter hostPath mounts.
Platform-owned ServiceMonitors may monitor application-owned Services without
both Argo instances owning the same object.

The shared manifests live under `deploy/base/applications` and
`deploy/base/platform-resources`; they are not duplicated. Existing Kind,
OpenShift, IBM, and hub entry points still compose the full deployment. Raw-file
consumers must update the moved paths, for example `deploy/base/controller.yaml`
is now `deploy/base/platform-resources/controller.yaml`. Repository scripts and
references have been updated with the moves.

## Consume from hypershell-gitops

Keep environment overlays and deployment decisions in `hypershell-gitops`.
For example, the platform overlay imports:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - github.com/openshift-online/hypershell/deploy/gitops/platform?ref=<full-commit-sha>
```

The application overlay imports:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - github.com/openshift-online/hypershell/deploy/gitops/applications?ref=<same-full-commit-sha>
```

Use the **same immutable source commit** for both packages. Pin compatible image
digests in the respective overlays, and promote them together where required.
These source bases retain existing image defaults; they are not production
release locks by themselves. Review all changes since the previous source pin
when adopting a newer commit, not just this refactor.

Split environment patches by owner too. Controller configuration, database-admin
credential delivery, SCC/RBAC subject namespaces, and monitoring customizations
belong to the platform overlay. API/console image and environment patches belong
to the application overlay. Do not copy the old combined overlay unchanged into
both Argo Applications.

The platform must install the required operators/CRDs, provision the namespace
and credentials, and configure the controller and shared dependencies. Keycloak,
databases, external secrets, shared ingress, and Argo's own configuration remain
external dependencies; these packages do not install them. AppProjects and
namespace access are configured separately in `hypershell-gitops`.

Argo sync waves do not provide a readiness barrier across two independent Argo
instances. Use an explicit platform readiness check before the initial
application sync.

## Controller ownership and isolation

The platform package deliberately keeps the privileged controller Deployment
with its ServiceAccount and RBAC. Allowing an application owner to replace that
Deployment would allow their code to run with the controller's permissions.

**This split is not a complete security boundary.** To preserve existing behavior,
the controller and application resources still default to `hypershell-system`.
Someone who can create arbitrary Pods in that namespace may be able to use the
controller's ServiceAccount; broad namespace access can also expose privileged
Secrets or allow changes to platform-owned namespaced resources.

Before granting application Argo access to a live namespace, agree on the trust
model and protect privileged identities and credentials through namespace
separation or appropriate admission/access controls. Namespace separation needs
additional application configuration and network-policy design; it is not
implemented here. AppProject restrictions alone do not solve runtime isolation.

## Adoption safety

- Existing `deploy/hub` consumers do not need to change immediately.
- Never reconcile `deploy/hub` alongside either split package for the same
  instance: they intentionally render overlapping resources.
- Before transfer, render the final environment overlays and compare resource
  identities/configuration. Ensure the split outputs are disjoint.
- Quiesce the old owner and any parent/ApplicationSet that could recreate it.
  Orphan-delete the old Application before the new owners sync; do not delete
  live workloads or enable pruning during the initial handoff.
- Production onboarding and any staging migration require separate GitOps PRs.

## Validate without a cluster

Requirements: Kustomize v5 and Python with PyYAML. CI pins Kustomize v5.8.1 and
PyYAML 6.0.3.

```sh
kustomize build deploy/gitops/platform
kustomize build deploy/gitops/applications
kustomize build deploy/hub
python3 scripts/test_gitops_manifests.py
```

Tests check the application kind allowlist, ServiceAccount selection, platform
ownership of the controller/monitoring, disjoint resource identities, equality
of the split composition and hub, namespace/name-prefix overlays, and builds of
existing deployment entry points. They do not install resources or assert live
operator compatibility or end-to-end runtime isolation.
