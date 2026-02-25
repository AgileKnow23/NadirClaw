"""Tests for the Windows service manager."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from nadirclaw.service_manager import (
    NADIRCLAW_DIR,
    NSSM_EXE,
    LOG_DIR,
    build_install_commands,
    build_uninstall_command,
    build_start_command,
    build_stop_command,
    build_status_command,
    get_service_configs,
)


class TestServiceConfigs:
    def test_has_both_services(self):
        configs = get_service_configs()
        assert "NadirClaw-SurrealDB" in configs
        assert "NadirClaw-Router" in configs

    def test_surrealdb_config_structure(self):
        configs = get_service_configs()
        surreal = configs["NadirClaw-SurrealDB"]
        assert "exe" in surreal
        assert "args" in surreal
        assert "description" in surreal
        assert surreal["depends_on"] is None

    def test_router_config_structure(self):
        configs = get_service_configs()
        router = configs["NadirClaw-Router"]
        assert "exe" in router
        assert "args" in router
        assert "description" in router
        assert router["depends_on"] == "NadirClaw-SurrealDB"

    def test_surrealdb_args_contain_start(self):
        configs = get_service_configs()
        assert "start" in configs["NadirClaw-SurrealDB"]["args"]

    def test_router_args_contain_nadirclaw(self):
        configs = get_service_configs()
        assert "nadirclaw" in configs["NadirClaw-Router"]["args"]

    def test_surrealdb_args_contain_bind(self):
        configs = get_service_configs()
        assert "0.0.0.0:8000" in configs["NadirClaw-SurrealDB"]["args"]

    def test_surrealdb_data_dir_uses_forward_slashes(self):
        configs = get_service_configs()
        args = configs["NadirClaw-SurrealDB"]["args"]
        # The file:// URI should use forward slashes
        assert "file://" in args


class TestNSSMCommandGeneration:
    @patch("nadirclaw.service_manager._find_nssm")
    def test_install_commands_structure(self, mock_nssm):
        mock_nssm.return_value = Path("C:/nssm.exe")
        nssm = str(Path("C:/nssm.exe"))
        config = {
            "exe": "C:/test/app.exe",
            "args": "--port 8000",
            "description": "Test service",
            "depends_on": None,
        }
        commands = build_install_commands("TestService", config)

        # Should have: install, description, stdout, stderr, rotate files,
        # rotate bytes, restart delay, start type, app directory
        assert len(commands) >= 9

        # First command should be the install
        assert commands[0] == [nssm, "install", "TestService", "C:/test/app.exe", "--port 8000"]

        # Check description is set
        desc_cmd = [c for c in commands if "Description" in c]
        assert len(desc_cmd) == 1
        assert "Test service" in desc_cmd[0]

    @patch("nadirclaw.service_manager._find_nssm")
    def test_install_commands_with_dependency(self, mock_nssm):
        mock_nssm.return_value = Path("C:/nssm.exe")
        config = {
            "exe": "C:/test/app.exe",
            "args": "--port 8000",
            "description": "Dependent service",
            "depends_on": "OtherService",
        }
        commands = build_install_commands("TestService", config)

        # Should include DependOnService command
        dep_cmd = [c for c in commands if "DependOnService" in c]
        assert len(dep_cmd) == 1
        assert "OtherService" in dep_cmd[0]

    @patch("nadirclaw.service_manager._find_nssm")
    def test_install_commands_log_rotation(self, mock_nssm):
        mock_nssm.return_value = Path("C:/nssm.exe")
        config = {
            "exe": "C:/test/app.exe",
            "args": "",
            "description": "Test",
            "depends_on": None,
        }
        commands = build_install_commands("TestService", config)

        # Check log rotation is configured
        rotate_cmd = [c for c in commands if "AppRotateFiles" in c]
        assert len(rotate_cmd) == 1
        assert "1" in rotate_cmd[0]

        bytes_cmd = [c for c in commands if "AppRotateBytes" in c]
        assert len(bytes_cmd) == 1
        assert "10485760" in bytes_cmd[0]

    @patch("nadirclaw.service_manager._find_nssm")
    def test_install_commands_auto_start(self, mock_nssm):
        mock_nssm.return_value = Path("C:/nssm.exe")
        config = {"exe": "app.exe", "args": "", "description": "", "depends_on": None}
        commands = build_install_commands("TestService", config)

        start_cmd = [c for c in commands if "SERVICE_AUTO_START" in c]
        assert len(start_cmd) == 1

    @patch("nadirclaw.service_manager._find_nssm")
    def test_install_commands_restart_delay(self, mock_nssm):
        mock_nssm.return_value = Path("C:/nssm.exe")
        config = {"exe": "app.exe", "args": "", "description": "", "depends_on": None}
        commands = build_install_commands("TestService", config)

        delay_cmd = [c for c in commands if "AppRestartDelay" in c]
        assert len(delay_cmd) == 1
        assert "5000" in delay_cmd[0]

    @patch("nadirclaw.service_manager._find_nssm")
    def test_uninstall_command(self, mock_nssm):
        mock_nssm.return_value = Path("C:/nssm.exe")
        nssm = str(Path("C:/nssm.exe"))
        cmd = build_uninstall_command("TestService")
        assert cmd == [nssm, "remove", "TestService", "confirm"]

    @patch("nadirclaw.service_manager._find_nssm")
    def test_start_command(self, mock_nssm):
        mock_nssm.return_value = Path("C:/nssm.exe")
        nssm = str(Path("C:/nssm.exe"))
        cmd = build_start_command("TestService")
        assert cmd == [nssm, "start", "TestService"]

    @patch("nadirclaw.service_manager._find_nssm")
    def test_stop_command(self, mock_nssm):
        mock_nssm.return_value = Path("C:/nssm.exe")
        nssm = str(Path("C:/nssm.exe"))
        cmd = build_stop_command("TestService")
        assert cmd == [nssm, "stop", "TestService"]

    @patch("nadirclaw.service_manager._find_nssm")
    def test_status_command(self, mock_nssm):
        mock_nssm.return_value = Path("C:/nssm.exe")
        nssm = str(Path("C:/nssm.exe"))
        cmd = build_status_command("TestService")
        assert cmd == [nssm, "status", "TestService"]


class TestServiceStatus:
    @patch("nadirclaw.service_manager._run")
    @patch("nadirclaw.service_manager._find_nssm")
    def test_status_running(self, mock_nssm, mock_run):
        from nadirclaw.service_manager import get_service_status
        mock_nssm.return_value = Path("C:/nssm.exe")
        mock_run.return_value = MagicMock(stdout="SERVICE_RUNNING\n", returncode=0)
        assert get_service_status("TestService") == "running"

    @patch("nadirclaw.service_manager._run")
    @patch("nadirclaw.service_manager._find_nssm")
    def test_status_stopped(self, mock_nssm, mock_run):
        from nadirclaw.service_manager import get_service_status
        mock_nssm.return_value = Path("C:/nssm.exe")
        mock_run.return_value = MagicMock(stdout="SERVICE_STOPPED\n", returncode=0)
        assert get_service_status("TestService") == "stopped"

    @patch("nadirclaw.service_manager._find_nssm")
    def test_status_no_nssm(self, mock_nssm):
        from nadirclaw.service_manager import get_service_status
        mock_nssm.return_value = None
        assert get_service_status("TestService") == "not_installed"

    @patch("nadirclaw.service_manager._run")
    @patch("nadirclaw.service_manager._find_nssm")
    def test_status_not_installed(self, mock_nssm, mock_run):
        from nadirclaw.service_manager import get_service_status
        mock_nssm.return_value = Path("C:/nssm.exe")
        mock_run.return_value = MagicMock(stdout="", returncode=3)
        assert get_service_status("TestService") == "not_installed"


class TestConstants:
    def test_nadirclaw_dir(self):
        assert str(NADIRCLAW_DIR).endswith(".nadirclaw")

    def test_nssm_exe_in_bin(self):
        assert "bin" in str(NSSM_EXE)
        assert str(NSSM_EXE).endswith("nssm.exe")

    def test_log_dir(self):
        assert "logs" in str(LOG_DIR)
