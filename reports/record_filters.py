def non_aligned_records(records):
    return records.exclude(dkim_aligned__iexact="pass").exclude(spf_aligned__iexact="pass")


def exclude_spf_passing_sources(records):
    return records.exclude(
        spf_result__iexact="pass",
        dkim_aligned__iexact="pass",
        spf_aligned__iexact="pass",
        disposition__iexact="none",
    )
