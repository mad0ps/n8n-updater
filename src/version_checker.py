"""Version checker for n8n using Docker Hub API."""

import re
import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

DOCKER_HUB_API = "https://hub.docker.com/v2/repositories/n8nio/n8n/tags"
N8N_IMAGE = "n8nio/n8n"

# Regex for semantic versioning (e.g., 1.70.0, 1.70.1)
SEMVER_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass
class VersionInfo:
    """Information about an n8n version."""
    
    version: str
    major: int
    minor: int
    patch: int
    digest: Optional[str] = None
    last_updated: Optional[str] = None
    
    @classmethod
    def from_tag(cls, tag: str, digest: str = None, last_updated: str = None) -> Optional["VersionInfo"]:
        """Parse version from Docker tag."""
        match = SEMVER_PATTERN.match(tag)
        if not match:
            return None
        
        major, minor, patch = map(int, match.groups())
        return cls(
            version=tag,
            major=major,
            minor=minor,
            patch=patch,
            digest=digest,
            last_updated=last_updated
        )
    
    def __str__(self) -> str:
        return self.version
    
    def __lt__(self, other: "VersionInfo") -> bool:
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VersionInfo):
            return False
        return (self.major, self.minor, self.patch) == (other.major, other.minor, other.patch)
    
    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch))


async def get_latest_version() -> Optional[VersionInfo]:
    """
    Get the latest stable n8n version from Docker Hub.
    
    Looks at the 'latest' tag and finds which version it corresponds to.
    
    Returns:
        VersionInfo for the latest stable version, or None if fetch failed.
    """
    try:
        async with aiohttp.ClientSession() as session:
            # Fetch tags including 'latest'
            params = {"page_size": 100}
            async with session.get(DOCKER_HUB_API, params=params) as response:
                if response.status != 200:
                    logger.error(f"Docker Hub API returned {response.status}")
                    return None
                
                data = await response.json()
        
        # Find the 'latest' tag and its digest
        latest_digest = None
        versions_by_digest: dict[str, VersionInfo] = {}
        
        for tag_info in data.get("results", []):
            tag = tag_info.get("name", "")
            digest = tag_info.get("digest", "")
            
            if tag == "latest":
                latest_digest = digest
                logger.debug(f"Found 'latest' tag with digest: {digest[:20]}...")
            
            # Also collect version tags
            version = VersionInfo.from_tag(
                tag,
                digest=digest,
                last_updated=tag_info.get("last_updated")
            )
            if version and digest:
                versions_by_digest[digest] = version
        
        # Find version that matches 'latest' digest
        if latest_digest and latest_digest in versions_by_digest:
            latest_version = versions_by_digest[latest_digest]
            logger.info(f"Latest tag corresponds to version {latest_version}")
            return latest_version
        
        # Fallback: if we can't match digest, look for the most recent version tag
        # based on last_updated timestamp
        if versions_by_digest:
            all_versions = list(versions_by_digest.values())
            # Sort by last_updated, most recent first
            all_versions.sort(
                key=lambda v: v.last_updated or "",
                reverse=True
            )
            if all_versions:
                logger.info(f"Fallback: using most recently updated version {all_versions[0]}")
                return all_versions[0]
        
        logger.error("Could not determine latest version")
        return None
        
    except aiohttp.ClientError as e:
        logger.error(f"Failed to fetch from Docker Hub: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error checking versions: {e}")
        return None


async def get_all_versions(limit: int = 20) -> list[VersionInfo]:
    """
    Get recent n8n versions from Docker Hub.
    
    Args:
        limit: Maximum number of versions to return.
        
    Returns:
        List of VersionInfo sorted by version descending.
    """
    try:
        async with aiohttp.ClientSession() as session:
            params = {"page_size": 100}
            async with session.get(DOCKER_HUB_API, params=params) as response:
                if response.status != 200:
                    logger.error(f"Docker Hub API returned {response.status}")
                    return []
                
                data = await response.json()
        
        versions: list[VersionInfo] = []
        
        for tag_info in data.get("results", []):
            tag = tag_info.get("name", "")
            version = VersionInfo.from_tag(
                tag,
                digest=tag_info.get("digest"),
                last_updated=tag_info.get("last_updated")
            )
            if version:
                versions.append(version)
        
        # Sort by version descending and limit
        versions.sort(reverse=True)
        return versions[:limit]
        
    except Exception as e:
        logger.exception(f"Failed to fetch versions: {e}")
        return []


def parse_version(version_str: str) -> Optional[VersionInfo]:
    """
    Parse a version string into VersionInfo.
    
    Args:
        version_str: Version string like "1.70.0" or "n8nio/n8n:1.70.0"
        
    Returns:
        VersionInfo or None if parsing failed.
    """
    # Handle full image reference
    if ":" in version_str:
        version_str = version_str.split(":")[-1]
    
    # Handle "v" prefix
    version_str = version_str.lstrip("v")
    
    return VersionInfo.from_tag(version_str)


def compare_versions(current: str, latest: str) -> int:
    """
    Compare two version strings.
    
    Returns:
        -1 if current < latest (update available)
         0 if current == latest (up to date)
         1 if current > latest (ahead of latest)
    """
    current_info = parse_version(current)
    latest_info = parse_version(latest)
    
    if current_info is None or latest_info is None:
        logger.warning(f"Could not parse versions: current={current}, latest={latest}")
        return 0
    
    if current_info < latest_info:
        return -1
    elif current_info == latest_info:
        return 0
    else:
        return 1
