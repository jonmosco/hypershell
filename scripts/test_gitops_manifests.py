#!/usr/bin/env python3
"""Render-only ownership contracts. Requires Kustomize v5 and PyYAML.

Run: python3 scripts/test_gitops_manifests.py
No cluster access or credentials are needed.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def identity(resource):
    """Kubernetes object identity, independent of served API version."""
    api_version = resource["apiVersion"]
    group = api_version.split("/")[0] if "/" in api_version else ""
    metadata = resource["metadata"]
    return group, resource["kind"], metadata.get("namespace", ""), metadata["name"]


def index_resources(resources):
    result = {}
    for resource in resources:
        if resource is None:
            continue
        key = identity(resource)
        if key in result:
            raise ValueError(f"Duplicate resource: {key}")
        result[key] = resource
    return result


def render(path, *, legacy=False):
    command = ["kustomize", "build"]
    # Existing dev overlays reference individual files outside their root.
    # The new GitOps packages must work with default load restrictions.
    if legacy:
        command.append("--load-restrictor=LoadRestrictionsNone")
    command.append(str(ROOT / path))
    output = subprocess.check_output(command, text=True)
    return index_resources(yaml.safe_load_all(output))


def render_overlay(path):
    # Keep the temporary overlay inside deploy so its relative resource paths
    # also exercise the normal layout used by downstream Kustomize consumers.
    with tempfile.TemporaryDirectory(prefix=".gitops-test-", dir=ROOT / "deploy") as temp:
        overlay = Path(temp)
        (overlay / "kustomization.yaml").write_text(yaml.safe_dump({
            "apiVersion": "kustomize.config.k8s.io/v1beta1",
            "kind": "Kustomization",
            "namespace": "hyp6",
            "namePrefix": "hyp6-",
            "resources": [f"../{path}"],
        }))
        return render(overlay)


class GitOpsManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.applications = render("deploy/gitops/applications")
        cls.platform = render("deploy/gitops/platform")
        cls.hub = render("deploy/hub")

    def test_application_resources_are_explicitly_namespaced(self):
        # Fail closed for new kinds; a metadata.namespace alone does not prove
        # that a resource is namespaced (Kustomize may put one on unknown CRs).
        allowed = {
            ("v1", "ServiceAccount"),
            ("v1", "Service"),
            ("apps/v1", "Deployment"),
            ("route.openshift.io/v1", "Route"),
        }
        self.assertTrue(self.applications)
        for key, resource in self.applications.items():
            with self.subTest(resource=key):
                self.assertIn((resource["apiVersion"], resource["kind"]), allowed)
                self.assertEqual(resource["metadata"]["namespace"], "hypershell-system")

    def test_application_deployments_use_only_their_own_service_accounts(self):
        accounts = {
            key[3] for key in self.applications if key[1] == "ServiceAccount"
        }
        self.assertEqual(accounts, {"hypershell-api-server", "hypershell-web-console"})
        deployments = [r for r in self.applications.values() if r["kind"] == "Deployment"]
        self.assertEqual({r["metadata"]["name"] for r in deployments}, accounts)
        for deployment in deployments:
            self.assertIn(deployment["spec"]["template"]["spec"]["serviceAccountName"], accounts)

    def test_platform_owns_controller_namespace_and_monitoring(self):
        for group, kind, namespace, name in [
            ("", "Namespace", "", "hypershell-system"),
            ("", "ServiceAccount", "hypershell-system", "hypershell-controller"),
            ("", "Service", "hypershell-system", "hypershell-controller"),
            ("apps", "Deployment", "hypershell-system", "hypershell-controller"),
            ("rbac.authorization.k8s.io", "ClusterRole", "", "hypershell-controller"),
            ("rbac.authorization.k8s.io", "ClusterRoleBinding", "", "hypershell-controller"),
            ("rbac.authorization.k8s.io", "ClusterRoleBinding", "", "hypershell-sandbox-scc"),
        ]:
            self.assertIn((group, kind, namespace, name), self.platform)
        for key, resource in render("deploy/base/prometheus").items():
            self.assertEqual(resource, self.platform[key])

    def test_packages_are_disjoint_and_compose_the_hub(self):
        self.assertFalse(self.applications.keys() & self.platform.keys())
        self.assertEqual(self.hub, {**self.platform, **self.applications})

    def test_namespace_and_name_prefix_overlays_preserve_composition(self):
        platform = render_overlay("gitops/platform")
        applications = render_overlay("gitops/applications")
        self.assertFalse(platform.keys() & applications.keys())
        self.assertEqual(render_overlay("hub"), {**platform, **applications})
        self.assertTrue(all(key[2] == "hyp6" for key in applications))

    def test_existing_entry_points_still_build(self):
        for path in ("base", "openshift", "ibm", "kind", "keycloak"):
            with self.subTest(path=path):
                self.assertTrue(render(f"deploy/{path}", legacy=True))

    def test_duplicate_detection_ignores_api_version(self):
        resource = {"apiVersion": "example.test/v1", "kind": "Example",
                    "metadata": {"name": "duplicate", "namespace": "test"}}
        with self.assertRaisesRegex(ValueError, "Duplicate resource"):
            index_resources([resource, {**resource, "apiVersion": "example.test/v2"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
