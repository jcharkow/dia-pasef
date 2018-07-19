import sys
import matplotlib
matplotlib.use('Agg')
import pandas as pd
import statsmodels.formula.api as sm
import numpy as np
from statsmodels.graphics.regressionplots import abline_plot
from matplotlib.backends.backend_pdf import PdfPages

def calibrate(data, plot = True, pdf = "rtcalibration"):
    """Fits a linear model to the irts"""
    filename = data.iloc[0,0]
    mod = sm.ols(formula = 'irt ~ rt', data = data)
    res = mod.fit()
    # scatter-plot data
    ax = data.plot(x='rt', y='irt', kind='scatter')
    a = abline_plot(model_results=res, ax=ax)
    # print(res.rsquared)
    a.suptitle("%s \n R2: %s \n R2adj: %s" % (filename, res.rsquared, res.rsquared_adj), fontsize = 10)
    # text(0.9, 0.1,("R2:"), ha='center', va='center', transform=ax.transAxes)
    a.savefig(pdf, format = 'pdf')
    matplotlib.pyplot.close()
    return [filename, res.params.Intercept, res.params.rt]
    # row = np.array([['raw', 'intercept', 'slope'], [data.iloc[0,0], res.params.Intercept, res.params.rt]])
    # return pd.DataFrame(data = row[1:,], columns = row[0,0:])

def get_product_charge(msl, msms):
    return(msl)

def pasef_to_tsv(evidence, msms, irt_file, pdfout = "rtcalibration.pdf", im_column = 'Ion mobility index'):
    """Converts a mq output to a library taking a best replicate approach."""
    if isinstance(irt_file, str):
        irt = pd.read_table(irt_file)
    elif isinstance(irt_file, pd.DataFrame):
        irt = irt_file
    else:
        print("irt_file must be a path to an irt table or a pd.DataFrame object.")
        sys.exit()

    ev = evidence.loc[:, ["id", "Calibrated Retention Time", "Ion mobility index", im_column]]
    ev = ev.rename(columns = {'id':'Evidence ID'})
    ev = ev.rename(columns = {im_column:'imcol'})
    ms = pd.merge(msms, ev, on = 'Evidence ID')
    ms = ms.dropna(subset=["Retention time"]) # Some precursors in MQ are not annotated with a RT

    # Replace modifications in the MQ output so that they are OpenMS readable
    ms['Modified sequence'] = ms['Modified sequence'].str.replace('_', '')
    ms['Modified sequence'] = ms['Modified sequence'].str.replace("(ox)","Oxidation")
    ms['Modified sequence'] = ms['Modified sequence'].str.replace("(ph)","Phospho")
    ms['Modified sequence'] = ms['Modified sequence'].str.replace("C","C(Carbamidomethyl)")
    ms['Modified sequence'] = ms['Modified sequence'].str.replace("(ac)","Acetylation")
    ms = ms.rename(columns = {'Modified sequence':'ModifiedPeptideSequence'})

    # Prepare iRT table
    if irt.shape[1] > 2:
        irt = irt.loc[:, ["ModifiedPeptideSequence", "NormalizedRetentionTime"]]
        irt = irt.drop_duplicates()

    irt.columns = ["sequence","irt"]
    irt['sequence'] = irt['sequence'].str.replace("\\(UniMod:4\\)","C(Carbamidomethyl)")
    irt['sequence'] = irt['sequence'].str.replace("\\(UniMod:1\\)","Acetylation")
    irt['sequence'] = irt['sequence'].str.replace("\\(UniMod:35\\)","Oxidation")
    irt['sequence'] = irt['sequence'].str.replace("\\(UniMod:21\\)","Phospho")

    # Use the irt peptides present in the MQ output to calibrate
    msms_irt = ms[ms["ModifiedPeptideSequence"].isin(irt["sequence"])]
    msms_irt = msms_irt.loc[msms_irt.groupby(["Raw file","ModifiedPeptideSequence"])['PEP'].idxmin()]
    msms_irt = msms_irt.loc[:, ["Raw file","ModifiedPeptideSequence","Retention time"]]
    msms_irt.columns = ["raw","sequence","rt"]
    msms_irt = pd.merge(msms_irt, irt, on = 'sequence')

    # Generate the iRT calibrators
    raw_files = msms_irt.raw.unique()
    calibrators = []
    pp = PdfPages(pdfout)
    for file in raw_files:
        cal = calibrate(msms_irt[msms_irt.raw == file], pdf = pp)
        calibrators.append(cal)
    pp.close()

    # Apply rt calibrators
    calibrators = pd.DataFrame(columns = ['Raw file', 'intercept', 'slope'], data = calibrators)
    ms = pd.merge(ms, calibrators, on = 'Raw file')
    ms['irt'] = ms.intercept + ms.slope * ms['Calibrated Retention Time']

    # Filter for best identification (lowest PEP)
    ms = ms.loc[ms.groupby(["ModifiedPeptideSequence", "Charge"])['PEP'].idxmin()]
    # Filter decoy peptides
    ms = ms[ms['Reverse'].isna()]

    # Shape data for AssayGenerator
    msl = ms.loc[:, ["id","m/z","Masses","Charge","irt", "imcol", "Intensities","Sequence","ModifiedPeptideSequence","Proteins"]]
    masses = msl['Masses'].str.split(';', expand = True).stack().str.strip().reset_index(level = 1, drop=True)
    intensities = msl['Intensities'].str.split(';', expand = True).stack().str.strip().reset_index(level = 1, drop=True)
    df1 = pd.concat([masses, intensities], axis = 1, keys = ['Masses', 'Intensities'])
    msl2 = msl.drop(['Masses', 'Intensities'], axis = 1)
    msl2 = msl2.join(df1).reset_index(drop = True)
    msl2.columns = ["transition_group_id","PrecursorMz","PrecursorCharge","Tr_recalibrated", "PrecursorIonMobility", "PeptideSequence","FullUniModPeptideName","ProteinName", "ProductMz", "LibraryIntensity"]
    msl2 = get_product_charge(msl2, msms)

    # Reorder the columns as they are in libraries generated with the OSW assay generator
    msl2 = msl2[["transition_group_id","PrecursorMz","ProductMz","PrecursorCharge","Tr_recalibrated","LibraryIntensity","PeptideSequence","FullUniModPeptideName","ProteinName", "PrecursorIonMobility"]]
    msl2['decoy'] = 0
    msl2['transition_name'] = ['_'.join(str(i) for i in z) for z in zip(msl2.transition_group_id,msl2.PrecursorMz,msl2.ProductMz)]

    msl2=msl2.drop_duplicates()
    # Write output
    # msl2.to_csv("pasefLib.tsv", sep = "\t", index=False)

    return(msl2)

# @TODO call the OpenSwathAssayGenerator with pyopenms
# def run_assay_generator(paseflib, window_scheme):
#     import pyopenms

