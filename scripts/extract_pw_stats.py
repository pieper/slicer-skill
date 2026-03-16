#!/usr/bin/env python3
"""
Script to extract statistics from NA-MIC Project Week repository
"""
import os
import re
from pathlib import Path

def count_participants_in_readme(readme_path):
    """Count participants from README.md file"""
    try:
        with open(readme_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Look for the participants list between comments
        start_pattern = r'<!-- Participants list start -->'
        end_pattern = r'<!-- Participants list end -->'

        start_match = re.search(start_pattern, content)
        end_match = re.search(end_pattern, content)

        if start_match and end_match:
            participants_section = content[start_match.end():end_match.start()]
            # Count lines that start with "1. " (participant entries)
            participant_lines = [line.strip() for line in participants_section.split('\n')
                               if line.strip().startswith('1. ')]
            return len(participant_lines)
        else:
            # Fallback: count lines with various participant patterns
            lines = content.split('\n')
            participant_count = 0
            in_participants = False
            for line in lines:
                if '## Registrants' in line or '## Participants' in line:
                    in_participants = True
                    continue
                if in_participants:
                    stripped = line.strip()
                    # Check for different formats
                    if stripped.startswith('1.') and ('\t' in stripped or ', ' in stripped):
                        participant_count += 1
                    elif re.match(r'^\d+\s+[^(\n]*\(', stripped):
                        participant_count += 1
                    elif stripped and not stripped.startswith('#') and not stripped.startswith('List of') and not stripped.startswith('Do not') and participant_count > 0:
                        # Stop when we hit non-participant content after starting participants
                        # But be more lenient - only stop on headers or empty sections
                        if stripped.startswith('##') or (not stripped and participant_count > 5):
                            break
            return participant_count
    except Exception as e:
        print(f"Error reading {readme_path}: {e}")
        return 0

def count_projects_in_directory(pw_dir):
    """Count projects - either from Projects/ subdir or from README"""
    projects_dir = pw_dir / "Projects"
    readme_path = pw_dir / "README.md"

    if projects_dir.exists():
        # New format: count subdirectories in Projects/
        try:
            subdirs = [d for d in projects_dir.iterdir() if d.is_dir() and d.name != "Template"]
            return len(subdirs)
        except:
            return 0
    else:
        # Old format: count projects in README
        try:
            with open(readme_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Look for projects section
            projects_match = re.search(r'## Projects(.*?)(?=##|\Z)', content, re.DOTALL)
            if projects_match:
                projects_content = projects_match.group(1)
                # Count lines that start with + or * (project entries)
                project_lines = [line for line in projects_content.split('\n')
                               if line.strip().startswith(('+ ', '* '))]
                return len(project_lines)
        except Exception as e:
            print(f"Error reading projects from {readme_path}: {e}")
            return 0

    return 0

def extract_participants_list(readme_path):
    """Extract list of participants with institutions"""
    try:
        with open(readme_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Look for the participants list between comments
        start_pattern = r'<!-- Participants list start -->'
        end_pattern = r'<!-- Participants list end -->'

        start_match = re.search(start_pattern, content)
        end_match = re.search(end_pattern, content)

        participants = []
        if start_match and end_match:
            participants_section = content[start_match.end():end_match.start()]
            lines = participants_section.split('\n')
            for line in lines:
                line = line.strip()
                if line.startswith('1. '):
                    # Extract name and institution
                    parts = line[3:].split(', ')
                    if len(parts) >= 2:
                        name = parts[0].strip()
                        institution = ', '.join(parts[1:]).strip()
                        participants.append((name, institution))
        else:
            # Fallback for older formats
            lines = content.split('\n')
            in_participants = False
            for line in lines:
                if '## Registrants' in line or '## Participants' in line:
                    in_participants = True
                    continue
                if in_participants:
                    stripped = line.strip()
                    if stripped.startswith('1.'):
                        if ', ' in stripped:
                            # Comma-separated format
                            parts = stripped[3:].split(', ')
                            if len(parts) >= 2:
                                name = parts[0].strip()
                                institution = ', '.join(parts[1:]).strip()
                                participants.append((name, institution))
                        elif '\t' in stripped:
                            # Tab-separated format
                            tab_parts = [p.strip() for p in stripped[3:].split('\t') if p.strip() and p.strip() != ',']
                            if len(tab_parts) >= 3:
                                name = tab_parts[0]
                                institution = tab_parts[1]
                                country = tab_parts[2] if len(tab_parts) > 2 else ""
                                full_institution = f"{institution}, {country}" if country else institution
                                participants.append((name, full_institution))
                    elif re.match(r'^\d+\s+[^(\n]*\(', stripped):
                        # Format like " 1 Isabella Morgan (Robarts Research Institute)"
                        match = re.match(r'^\d+\s+([^(\n]+)\s*\(([^)\n]+)\)', stripped)
                        if match:
                            name = match.group(1).strip()
                            institution = match.group(2).strip()
                            participants.append((name, institution))
                    elif stripped and not stripped.startswith('#') and not stripped.startswith('List of') and not stripped.startswith('Do not') and participants:
                        # Stop condition
                        if stripped.startswith('##') or (not stripped and len(participants) > 5):
                            break
        return participants
    except Exception as e:
        print(f"Error extracting participants from {readme_path}: {e}")
        return []

def extract_projects_list(pw_dir):
    """Extract list of projects"""
    projects_dir = pw_dir / "Projects"
    readme_path = pw_dir / "README.md"

    projects = []

    if projects_dir.exists():
        # New format: get project names from directory names
        try:
            subdirs = [d for d in projects_dir.iterdir() if d.is_dir() and d.name != "Template"]
            projects = []
            for d in subdirs:
                # Convert camelCase/hyphen-case to readable title
                name = d.name
                # Replace hyphens with spaces
                name = name.replace('-', ' ')
                # Add spaces before capital letters (for camelCase)
                name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
                # Title case
                name = name.title()
                projects.append(name)
        except:
            pass
    else:
        # Old format: extract from README
        try:
            with open(readme_path, 'r', encoding='utf-8') as f:
                content = f.read()

            projects_match = re.search(r'## Projects(.*?)(?=##|\Z)', content, re.DOTALL)
            if projects_match:
                projects_content = projects_match.group(1)
                for line in projects_content.split('\n'):
                    line = line.strip()
                    if line.startswith(('+ ', '* ')):
                        project_name = line[2:].strip()
                        # Clean up the project name
                        if '(' in project_name:
                            project_name = project_name.split('(')[0].strip()
                        projects.append(project_name)
        except Exception as e:
            print(f"Error extracting projects from {readme_path}: {e}")

    return projects

def get_pw_number(dirname):
    match = re.search(r'PW(\d+)', dirname)
    return int(match.group(1)) if match else 0


def main():
    base_dir = Path("/Users/pieper/slicer/slicer-skill/slicer-projectweek")

    # Read the main README to get all project weeks
    readme_path = base_dir / "README.md"
    with open(readme_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Extract the table
    table_match = re.search(r'## Past Project Weeks.*?\| Events \| Registrants \|.*?\|----\|----\|(.*?)---', content, re.DOTALL)
    if not table_match:
        print("Could not find project weeks table")
        return

    table_content = table_match.group(1)
    pw_entries = []

    for line in table_content.split('\n'):
        line = line.strip()
        if line and not line.startswith('|----') and '|' in line:
            parts = [p.strip() for p in line.split('|') if p.strip()]
            if len(parts) >= 2:
                # Extract PW number, year, and participants
                events_part = parts[0]
                registrants_part = parts[1] if len(parts) > 1 else "0"

                # Extract PW number
                pw_match = re.search(r'Project Week (\d+)', events_part)
                if pw_match:
                    pw_num = int(pw_match.group(1))
                    year_match = re.search(r'(\d{4})', events_part)
                    year = year_match.group(1) if year_match else "Unknown"

                    # Extract participant count
                    participants = 0
                    if registrants_part.isdigit():
                        participants = int(registrants_part)
                    else:
                        # Handle cases like "204 |" where it's at the end
                        num_match = re.search(r'(\d+)', registrants_part)
                        if num_match:
                            participants = int(num_match.group(1))

                    pw_entries.append((pw_num, year, participants))

    # Sort by PW number descending
    pw_entries.sort(key=lambda x: x[0], reverse=True)

    print("# NA-MIC Project Week Statistics\n")
    print("| Project Week | Year | Participants |")
    print("|-------------|------|-------------|")

    total_participants = 0
    all_participants = []

    for pw_num, year, participants in pw_entries:
        pw_name = f"PW{pw_num}"
        print(f"| {pw_name} | {year} | {participants} |")
        total_participants += participants

    # Now get project and detailed participant data from directories
    pw_dirs = [d for d in base_dir.iterdir() if d.is_dir() and d.name.startswith('PW')]

    def get_pw_number(dirname):
        match = re.search(r'PW(\d+)', dirname)
        return int(match.group(1)) if match else 0

    pw_dirs.sort(key=lambda x: get_pw_number(x.name), reverse=True)

    total_projects = 0
    for pw_dir in pw_dirs:
        projects_count = count_projects_in_directory(pw_dir)
        total_projects += projects_count

        # Get participants list
        readme_path = pw_dir / "README.md"
        if readme_path.exists():
            participants_list = extract_participants_list(readme_path)
            all_participants.extend(participants_list)

    print("\n## Summary Statistics")
    print(f"- **Total Project Weeks**: {len(pw_entries)}")
    print(f"- **Total Registered Participants**: {total_participants}")
    print(f"- **Total Projects**: {total_projects}")
    print(f"- **Average Participants per Week**: {total_participants/len(pw_entries):.1f}")
    if pw_dirs:
        print(f"- **Average Projects per Week**: {total_projects/len(pw_dirs):.1f}")

    # Unique participants (approximate - based on name matching)
    unique_participants = len(set(name for name, _ in all_participants))
    print(f"- **Unique Participant Names**: {unique_participants} (approximate, from {len(pw_dirs)} project weeks with detailed data)")

    print("\n## Sample Participants")
    for i, (name, institution) in enumerate(all_participants[:10]):
        print(f"{i+1}. {name}, {institution}")
    if len(all_participants) > 10:
        print(f"... and {len(all_participants) - 10} more")

    print("\n## Sample Projects")
    # Get some sample projects from the directories we have
    sample_projects = []
    for pw_dir in pw_dirs[:3]:  # Just from first few directories
        projects = extract_projects_list(pw_dir)
        sample_projects.extend(projects[:3])  # 3 from each

    for i, project in enumerate(sample_projects[:10]):
        print(f"{i+1}. {project}")
    if len(sample_projects) > 10:
        print(f"... and {len(sample_projects) - 10} more")

if __name__ == "__main__":
    main()